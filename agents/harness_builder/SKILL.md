You are helping debug and fix a failed harness compilation/link probe for a C/C++ library.

Your goal: make compile_harnesses.sh succeed so that the probe harness compiles and links
against the library's static artifacts, producing a binary in out/.

## What you have

The failure context will be appended below. It includes:
- The install directory containing static libraries (install/lib/*.a) and headers (install/include/)
- The work directory containing compile_harnesses.sh and harness_src/
- The static libraries already discovered
- Any link flags already auto-resolved from known symbol patterns
- Any missing system libraries already reported by the linker
- The complete stderr from the failed compile/link attempt (last 200 lines)

workdir is not a scratch directory — it is the real project directory that generation
copies its final output from (for oss-fuzz, it's the same directory that becomes the
shipped project). Edits you make here to compile_harnesses.sh, a Dockerfile, or other
files persist into that output.

## Reproducibility

The verification command — and every future rebuild of this project, on any machine,
months from now — only ever sees the files saved in workdir plus a fresh `git clone` of
the library's source. Two rules follow from that:

- **Persist every fix to disk, and only to disk.** Nothing outside what's saved in
  `compile_harnesses.sh` (or a Dockerfile/patch file it invokes) survives to the next
  run — not a command typed directly in this shell, not an exported env var, not a
  file edited by hand outside what the script itself does. The script must succeed
  standalone against a fresh `install/`, with no leftover state from this or any prior
  session. If a fix needs an env var, a package, or a symlink, encode it into
  compile_harnesses.sh (or the Dockerfile) so a fresh checkout reproduces it identically.
- **Hand-edits to the source tree do not persist.** Both the local `setup.sh` and the
  shipped oss-fuzz `Dockerfile` re-clone the library from git at build time — the copy
  of the source sitting in workdir right now is discarded the moment you exit. If a fix
  genuinely requires changing a source file (e.g. a missing header the harness needs),
  encode that change as a step compile_harnesses.sh performs itself — e.g. a `sed -i`
  invocation, or `patch < "$SCRIPT_DIR/some.patch"` where the patch file lives next to
  compile_harnesses.sh — not a one-off edit to the file in the source directory.

Also: use the script's own `$SCRIPT_DIR`/`$BUILD_PREFIX`/`$INSTALL_DIR` variables
(already defined near the top of compile_harnesses.sh) instead of baking in this
session's actual filesystem paths (e.g. `/home/user/.harnessbuddy/...`) — those paths
won't exist on a different machine or in a fresh container.

## What to do

1. Read compile_harnesses.sh in the work directory to understand the link command that was
   attempted (STATIC_LIBS array, EXTRA_LINK_FLAGS, compiler invocation).
2. Read the linker/compiler errors in the failure context to identify unresolved symbols
   or missing libraries.
3. Diagnose the failure:
   - Undefined symbols usually mean a transitive dependency's `-lXXX` flag is missing from
     EXTRA_LINK_FLAGS, or static library link order matters for this linker.
   - "library not found" / "cannot find -lXXX" errors mean a system library isn't
     installed on this machine.
4. Decide how to fix it, based on whether the library is resolvable on this machine:
   - **If adding a `-lXXX` flag lets the build compile and link successfully right now**
     (the library is already present on this machine, or an alternative like a static
     archive already in install/lib, or a different link order resolves it): make the
     fix yourself — edit EXTRA_LINK_FLAGS, include paths, or static library link order
     directly in compile_harnesses.sh — and verify it. Still report the flag in
     `missing_libs`/`missing_apt_packages`/`missing_brew_packages` in `agent_report.json`
     (see below), even though it already worked here — HarnessBuddy needs the package
     name recorded for portability to environments that don't already have it.
   - **If the library isn't resolvable on this machine at all** (nothing to link
     against; installing a system package is unavoidable): do not edit
     EXTRA_LINK_FLAGS yourself, since you cannot verify a fix you cannot compile.
     Instead, identify the bare library name and report it via `missing_libs` (see
     below), determine the actual apt and brew package names independently (see
     `agent_report.json` fields), print:
     "ACTION REQUIRED: Missing system packages detected. Please review agent_report.json
      and install the listed packages, then re-run this agent."
     Write `agent_report.json`, exit with a non-zero status code, and do not proceed
     further.
5. Once you've made a fix, run the verification command given in the failure context below
   (not compile_harnesses.sh directly) to confirm it now succeeds in the selected target
   environment and a binary appears in out/.

## `agent_report.json`

Before exiting — on **every** outcome, success or stop-for-human-action — write
`agent_report.json` to the work directory (the directory containing compile_harnesses.sh)
with this shape:

```json
{
  "summary": "libfoo.a alone is missing zlib symbols; linked against the system zlib at /usr/lib/x86_64-linux-gnu instead of requesting a new package.",
  "missing_libs": [],
  "missing_apt_packages": [],
  "missing_brew_packages": [],
  "extra_include_paths": ["./src"],
  "extra_library_paths": ["./src/build"]
}
```

- `summary` (string): plain-language description of what you diagnosed/did. Always
  include this, on both success and failure. If uncertain of an exact package name,
  say so here rather than asserting it confidently.
- `missing_libs` (array of strings): bare library name(s) — the part after `-l` — for
  every new `-lXXX` flag you added to EXTRA_LINK_FLAGS, whether or not installing a
  package was needed to make it work. Empty array if you added no new flags.
- `missing_apt_packages` (array of strings): Debian/Ubuntu `apt-get install` package
  names that provide the libraries above (e.g. `"libldap2-dev"` for `-lldap`), reported
  alongside every entry in `missing_libs` — including ones already present on this
  machine, for portability to environments that aren't.
- `missing_brew_packages` (array of strings): Homebrew `brew install` formula names for
  the same dependencies (e.g. `"openldap"` for `-lldap`). Often a different string than
  the apt package or the library name itself — work out each independently rather than
  reusing one name across all three.
- `extra_include_paths` (array of strings): relative paths, outside `install/include`,
  that you added to the harness compile command's `-I` search path to make the fix work.
  Empty array if none.
- `extra_library_paths` (array of strings): relative paths, outside `install/lib`, that
  you added to the harness link command's `-L` search path to make the fix work (e.g. a
  system library location). Empty array if none.

Omit fields you have nothing to report for — they default to empty/absent on the
reading side.

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