You are helping debug and fix a failed C/C++ static library build.

Your goal: make the build_library.sh script succeed so that:
- static libraries (*.a) are installed to install/lib/
- headers are installed to install/include/

## What you have

The build failure context will be appended below. It includes:
- The source directory path
- The detected build system
- The build command that was run
- The complete stdout/stderr from the failed build
- The expected install directory paths

workdir (the directory build_library.sh lives in) is not a scratch directory — it is the
real project directory that generation copies its final output from (for oss-fuzz, it's
the same directory that becomes the shipped project). Edits you make here to
build_library.sh, a Dockerfile, or other files persist into that output.

## Reproducibility

The verification command — and every future rebuild of this project, on any machine,
months from now — only ever sees the files saved in workdir plus a fresh `git clone` of
the library's source. Two rules follow from that:

- **Persist every fix to disk, and only to disk.** Nothing outside what's saved in
  `build_library.sh` (or a Dockerfile/patch file it invokes) survives to the next run —
  not a command typed directly in this shell, not an exported env var, not a file
  edited by hand outside what the script itself does. The script must succeed
  standalone against a freshly cloned source tree, with no leftover state from this or
  any prior attempt (a partially-built `build/` directory, a file created by hand). If
  a fix needs an env var, a package, or a symlink, encode it into build_library.sh (or
  the Dockerfile) so a fresh checkout reproduces it identically.
- **Hand-edits to the source tree do not persist.** Both the local `setup.sh` and the
  shipped oss-fuzz `Dockerfile` re-clone the library from git at build time — the copy
  of the source sitting in workdir right now is discarded the moment you exit. If a fix
  genuinely requires changing a source file (a broken CMakeLists.txt, a workaround for a
  compiler-version bug in a `.c` file), encode that change as a step build_library.sh
  performs itself — e.g. a `sed -i` invocation, or `patch < "$SCRIPT_DIR/some.patch"`
  where the patch file lives next to build_library.sh — not a one-off edit to the file
  in the source directory.

Also: use the script's own `$SCRIPT_DIR`/`$BUILD_PREFIX`/`$INSTALL_DIR` variables
(already defined near the top of build_library.sh) instead of baking in this session's
actual filesystem paths (e.g. `/home/user/.harnessbuddy/...`) — those paths won't exist
on a different machine or in a fresh container.

## What to do

1. Read build_library.sh in the work directory to understand what was attempted.
2. Read the relevant build files (CMakeLists.txt, Makefile, configure.ac, meson.build, etc.).
3. Diagnose the failure from the build output.
4. Fix the problem: modify build_library.sh or other build files as needed.
   Common fixes: wrong static library flags, configure options, disabling optional features.
5. If missing system packages are the root cause, first try to disable the feature that
   requires them via build flags (e.g. -DWITH_SSL=OFF, --disable-ssl, -Doption=disabled).
   Prefer this over requesting package installation — optional features are not needed for
   the core static library. Only escalate to package installation if the missing dependency
   is critical to the library's core functionality (not just for optional functionality).
6. If a required package cannot be avoided:
   - Determine the actual installable package name for both Debian/Ubuntu (`apt-get`) and
     Homebrew (`brew`) from your own knowledge. These are frequently different from each
     other and from the library name itself — e.g. OpenLDAP is `libldap2-dev` on apt but
     `openldap` on brew. Do not guess a single name and reuse it for both; work out each
     independently. If uncertain of the exact package name, say so in `summary` rather
     than asserting it confidently. Do not install anything yourself.
   - Print a clear message to stdout explaining what is needed:
     "ACTION REQUIRED: Missing system packages detected. Please review agent_report.json
      and install the listed packages, then re-run this agent."
   - Write `agent_report.json` (see below), listing both package names, before exiting.
   - Exit with a non-zero status code to signal incomplete execution.
   - Do not proceed further.
7. Once any required packages are installed, run the verification command given in the
   failure context below (not build_library.sh directly) to confirm the fix works in the
   selected target environment. It is the authoritative check — it also compiles a stub
   harness against your install/ output — so trust its exit code over eyeballing
   install/lib and install/include yourself.

## `agent_report.json`

Before exiting — on **every** outcome, success or stop-for-human-action — write
`agent_report.json` to the work directory (the directory containing build_library.sh,
**not** the source directory) with this shape:

```json
{
  "summary": "Disabled optional SSL support via -DWITH_SSL=OFF; libssl-dev was unavailable.",
  "missing_apt_packages": ["libssl-dev", "libz-dev"],
  "missing_brew_packages": ["openssl", "zlib"],
  "extra_include_paths": ["./src/"],
  "extra_library_paths": ["./src/build"]
}
```

- `summary` (string): plain-language description of what you diagnosed/did. Always
  include this, on both success and failure.
- `missing_apt_packages` (array of strings): Debian/Ubuntu `apt-get install` package
  names still needed (e.g. `"libssl-dev"`). Empty array if none. Correctness is of
  utmost importance — do not list a package unless you are sure it exists.
- `missing_brew_packages` (array of strings): Homebrew `brew install` formula names for
  the same dependencies (e.g. `"openssl"`). Often a different string than the apt
  package — work out both independently rather than reusing one for the other. Empty
  array if none.
- `extra_include_paths` (array of strings): relative paths, outside `install/include`,
  that a downstream harness-compilation step should add to its `-I` search path if you
  discovered the build needs one. Empty array if none.
- `extra_library_paths` (array of strings): relative paths, outside `install/lib`, that
  a downstream harness-compilation step should add to its `-L` search path if you
  discovered the build needs one. Empty array if none.

Omit fields you have nothing to report for a given field — they default to empty/absent
on the reading side. This is the only machine-readable report file you should write.

## Stopping conditions

**Success** — the verification command exits 0. Write `agent_report.json` first, then
print a short success summary and exit 0.

**Unresolvable failure** — if the verification command still fails after your fix
attempt, or if you cannot determine a fix, stop immediately. Print to stdout:
- What you diagnosed as the root cause
- What fix(es) you attempted (if any)
- The exact error from the build output

Write `agent_report.json` first, then exit with a non-zero status code. Do not retry
indefinitely or attempt speculative fixes beyond what the evidence supports.

## Important: non-interactive execution

This agent may be run non-interactively (e.g. via `claude --print`). In that
mode there is no user to respond to mid-run prompts. Never pause to ask the
user a question. If human input is required (e.g. to install packages), write
all necessary context to stdout, write any helper scripts to disk, and exit
with a non-zero status so the caller can detect and surface the situation.