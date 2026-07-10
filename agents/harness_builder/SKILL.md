You are helping debug and fix a failed harness compilation/link probe for a C/C++ library.

Your goal: make build_harness.sh succeed so that the probe harness compiles and links
against the library's static artifacts, producing a binary in out/.

## What you have

The failure context will be appended below. It includes:
- The install directory containing static libraries (install/lib/*.a) and headers (install/include/)
- The work directory containing build_harness.sh and harness_src/
- The static libraries already discovered
- Any link flags already auto-resolved from known symbol patterns
- Any missing system libraries already reported by the linker
- The complete stderr from the failed compile/link attempt (last 200 lines)

## What to do

1. Read build_harness.sh in the work directory to understand the link command that was
   attempted (STATIC_LIBS array, EXTRA_LINK_FLAGS, compiler invocation).
2. Read the linker/compiler errors in the failure context to identify unresolved symbols
   or missing libraries.
3. Diagnose the failure:
   - Undefined symbols usually mean a transitive dependency's `-lXXX` flag is missing from
     EXTRA_LINK_FLAGS, or static library link order matters for this linker.
   - "library not found" / "cannot find -lXXX" errors mean a system library isn't
     installed on this machine.
4. Fix the problem: edit build_harness.sh directly — add missing `-lXXX` flags to
   EXTRA_LINK_FLAGS, fix include paths, or adjust the static library link order.
   Any time you add a new `-lXXX` flag here, also report it in `agent_report.json`'s
   `missing_libs`/`missing_apt_packages`/`missing_brew_packages` (see below) — even if the
   library was already present on this machine and the fix works immediately with nothing
   to install. HarnessBuddy needs the package name recorded for portability to environments
   that don't already have it, regardless of whether this one did.
5. If a missing system package is the root cause:
   - Prefer finding an alternative (a static archive already in install/lib, a different
     link order) before requesting package installation.
   - If a package is unavoidable, identify the bare library name (the part after `-l`,
     e.g. `ldap` for a `-lldap` flag) and report it via `missing_libs` in
     `agent_report.json` (see below) — do not edit EXTRA_LINK_FLAGS in build_harness.sh
     yourself for this flag, since you cannot verify a fix you cannot compile; harnessbuddy
     adds the flag once you report the bare name.
   - Determine the actual installable package name for both Debian/Ubuntu (`apt-get`) and
     Homebrew (`brew`) from your own knowledge. These are frequently different from each
     other and from the library name itself — e.g. `-lldap` comes from `libldap2-dev` on
     apt but `openldap` on brew. Do not guess a single name and reuse it for both; work
     out each independently. Do not install anything yourself.
   - Print a clear message to stdout explaining what is needed:
     "ACTION REQUIRED: Missing system packages detected. Please review agent_report.json
      and install the listed packages, then re-run this agent."
   - Write `agent_report.json` (see below), listing the library name and both package
     names, before exiting.
   - Exit with a non-zero status code to signal incomplete execution.
   - Do not proceed further.
6. Once you've made a fix, run the verification command given in the failure context below
   (not build_harness.sh directly) to confirm it now succeeds in the selected target
   environment and a binary appears in out/.

Stop as soon as the harness links successfully and a binary is in place.

## `agent_report.json`

Before exiting — on **every** outcome, success or stop-for-human-action — write
`agent_report.json` to the work directory (the directory containing build_harness.sh)
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
  include this, on both success and failure.
- `missing_libs` (array of strings): bare library name(s) — the part after `-l` — for
  every new `-lXXX` flag you added to EXTRA_LINK_FLAGS (e.g. `"ldap"` for a `-lldap`
  flag), whether or not installing a package was needed to make it work. Empty array if
  you added no new flags.
- `missing_apt_packages` (array of strings): Debian/Ubuntu `apt-get install` package
  names that provide the libraries above (e.g. `"libldap2-dev"`), reported alongside
  every entry in `missing_libs` — including ones already present on this machine, for
  portability to environments that aren't. Empty array if none.
- `missing_brew_packages` (array of strings): Homebrew `brew install` formula names for
  the same dependencies (e.g. `"openldap"`). Often a different string than the apt
  package — work out both independently rather than reusing one for the other. Empty
  array if none.
- `extra_include_paths` (array of strings): relative paths, outside `install/include`,
  that you added to the harness compile command's `-I` search path to make the fix work.
  Empty array if none.
- `extra_library_paths` (array of strings): relative paths, outside `install/lib`, that
  you added to the harness link command's `-L` search path to make the fix work (e.g. a
  system library location). Empty array if none.

Omit fields you have nothing to report for a given field — they default to empty/absent
on the reading side. This is the only machine-readable report file you should write.

## Stopping conditions

**Success** — the verification command exits 0 and out/ contains the compiled probe
binary. Print a short success summary and exit 0. Write `agent_report.json` first.

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
