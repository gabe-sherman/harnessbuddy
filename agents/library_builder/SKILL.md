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
     independently. Do not install anything yourself.
   - Print a clear message to stdout explaining what is needed:
     "ACTION REQUIRED: Missing system packages detected. Please review agent_report.json
      and install the listed packages, then re-run this agent."
   - Write `agent_report.json` (see below), listing both package names, before exiting.
   - Exit with a non-zero status code to signal incomplete execution.
   - Do not proceed further.
7. Once any required packages are installed, run the verification command given in the
   failure context below (not build_library.sh directly) to confirm the fix works in the
   selected target environment.
8. Confirm that install/lib/*.a and install/include/* now exist.

Stop as soon as the build succeeds and artifacts are in place.

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
  utmost importance, do not write a package instalation unless you are sure it exists.s
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

**Success** — the verification command exits 0 and both install/lib/*.a and
install/include/* exist. Print a short success summary and exit 0. Write
`agent_report.json` first.

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