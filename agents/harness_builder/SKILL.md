You are helping debug and fix a failed harness compilation/link probe for a C/C++ library.

Your goal: make compile_harness.sh succeed so that a supplied harness compiles and links
against the library's static artifacts, producing a binary in out/.

## What you have

The failure context will be appended below. It includes:
- The install directory containing static libraries (install/lib/*.a) and headers (install/include/)
- The work directory containing compile_harness.sh and harness_src/
- The static libraries already discovered
- Any link flags already auto-resolved from known symbol patterns
- Any missing system libraries already reported by the linker
- The complete stderr from the failed compile/link attempt (last 200 lines)

workdir is not a scratch directory — it is the real project directory that generation
copies its final output from (for oss-fuzz, it's the same directory that becomes the
shipped project). Edits you make here to compile_harness.sh, a Dockerfile, or other
files persist into that output.

## Reproducibility

The verification command — and every future rebuild of this project, on any machine,
months from now — only ever sees the files saved in workdir plus a fresh `git clone` of
the library's source. Two rules follow from that:

- **Persist every fix to disk, and only to disk.** Nothing outside what's saved in
  `compile_harness.sh` (or a Dockerfile/patch file it invokes) survives to the next
  run — not a command typed directly in this shell, not an exported env var, not a
  file edited by hand outside what the script itself does. The script must succeed
  standalone against a fresh `install/`, with no leftover state from this or any prior
  session. If a fix needs an env var, a package, or a symlink, encode it into
  compile_harness.sh (or the Dockerfile) so a fresh checkout reproduces it identically.
- **Hand-edits to the source tree do not persist.** Both the local `setup.sh` and the
  shipped oss-fuzz `Dockerfile` re-clone the library from git at build time — the copy
  of the source sitting in workdir right now is discarded the moment you exit. If a fix
  genuinely requires changing a source file (e.g. a missing header the harness needs),
  encode that change as a step compile_harness.sh performs itself — e.g. a `sed -i`
  invocation, or `patch < "$SCRIPT_DIR/some.patch"` where the patch file lives next to
  compile_harness.sh — not a one-off edit to the file in the source directory.

Also: use the script's own `$SCRIPT_DIR`/`$BUILD_PREFIX`/`$INSTALL_DIR` variables
(already defined near the top of compile_harness.sh) instead of baking in this
session's actual filesystem paths (e.g. `/home/user/.harnessbuddy/...`) — those paths
won't exist on a different machine or in a fresh container.

## What to do

1. Read compile_harness.sh in the work directory to understand the link command that was
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
   directly in compile_harness.sh — and verify it. Still report the flag in
     `missing_libs`/`missing_apt_packages`/`missing_brew_packages` in `agent_report.json`
     (see below), even though it already worked here — HarnessBuddy needs the package
     name recorded for portability to environments that don't already have it.
   - **If the library isn't resolvable on this machine at all** (nothing to link
     against; installing a system package is unavoidable): do not edit
   EXTRA_LINK_FLAGS yourself, since you cannot verify a fix you cannot compile.
     Instead, identify the bare library name and report it via `missing_libs` (see
     below), determine the actual apt and brew package names independently (see
     `agent_report.json` fields), and say in your own reply text:
     "ACTION REQUIRED: Missing system packages detected. Please review agent_report.json
      and install the listed packages, then re-run this agent."
     Write that line yourself — not through `echo` or any other command, and not only
     inside a file. The caller reads the marker from your response text only.
     Write `agent_report.json`, and do not proceed further.
5. Once you've made a fix, run the verification command given in the failure context below
   (not compile_harness.sh directly) to confirm it now succeeds in the selected target
   environment and a binary appears in out/.

## `agent_report.json`

Before exiting — on **every** outcome, success or stop-for-human-action — write
`agent_report.json` to the work directory (the directory containing compile_harness.sh)
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

You cannot signal an outcome through your process exit code — the runner invokes you via
`claude --print`, which exits 0 whenever the CLI itself ran, no matter how you decided to
stop. The `ACTION REQUIRED` line and `agent_report.json` are the only signals the caller
reads, so a stop that needs human action is detected *only* if you write that exact
marker.

The caller scans your response text for the marker — not the command output or file
contents in your transcript. So the marker counts only when *you* write it, and quoting
it while explaining something (including quoting this document) does not accidentally
signal a stop.

**Success** — the verification command exits 0. Write `agent_report.json` first, then
give a short success summary.

**Blocked on human action** (a library that must be installed) — write the exact
`ACTION REQUIRED:` line from step 4 and write `agent_report.json` with the package names.

**Unresolvable failure** — if the verification command still fails after your fix
attempt, or if you cannot determine a fix, stop immediately. Write `agent_report.json`
first, then report:
- What you diagnosed as the root cause
- What fix(es) you attempted (if any)
- The exact error from the build output

Do not retry indefinitely or attempt speculative fixes beyond what the evidence supports.
The caller detects this case by checking `out/` for a linked binary itself, so no marker is
needed — but put the diagnosis in `summary` so it reaches the failure report.

## Important: non-interactive execution

This agent may be run non-interactively (e.g. via `claude --print`). In that
mode there is no user to respond to mid-run prompts. Never pause to ask the
user a question. If human input is required (e.g. to install packages), put all
necessary context in your reply, write any helper scripts to disk, and write the
`ACTION REQUIRED` marker yourself so the caller can detect and surface the situation.
