# Quickstart: Validating Clear Build Logging and Diagnostics

Manual end-to-end checks for this feature, once implemented. See
`contracts/cli-console-contract.md` for the exact output rules being
validated and `data-model.md` for the `Phase`/`FailureDiagnostic` shapes
referenced below.

## Prerequisites

- HarnessBuddy installed in a `uv` environment (`uv run harnessbuddy ...`).
- Network access and the usual local build toolchain (cmake/make/autotools/
  meson) per `CLAUDE.md`, since these scenarios run real builds.
- A known-good library repo (e.g. `zlib`) and a way to produce a known-bad
  one (e.g. a local copy of a small repo with its build script edited to
  fail deterministically, or an unsupported/empty repo).

## Scenario 1 — Phase visibility on a successful run (Story 1)

```bash
uv run harnessbuddy generate <zlib-repo-url> --output /tmp/hb-out-good
```

**Expected**: stdout shows a start line and an end line for each phase in
order — repository ingestion, static analysis, static library build, harness
compile probe, output generation (agent-repair phases do not appear, since
nothing failed) — with each phase's full raw subprocess output streaming
live in between, exactly as it does today. The start/end banners use a
distinctive separator (not just wording) so they're easy to spot while
scrolling past that raw output. Scanning for banners alone (without needing
to read or parse the streamed build-tool output itself) is enough to name
every phase the run passed through, satisfying SC-001.

## Scenario 2 — Default failure diagnostic (Story 2)

```bash
uv run harnessbuddy generate <repo-with-broken-build-script> --output /tmp/hb-out-bad
```

**Expected**: the static library build phase's raw output streams live as it
runs (as in Scenario 1), its end line reports failure, and a diagnostic
block follows naming the phase ("Static library build"), the specific
failing step, a human-readable message, and a log file path under
`.harnessbuddy/<project>/logs/`. The diagnostic itself does not repeat the
raw output a second time (it already streamed above); opening the log file
shows the same complete raw build output for later reference (SC-002).

## Scenario 2b — `--quiet` suppresses streaming, not banners or diagnostics (Story 1/2)

```bash
uv run harnessbuddy generate <repo-with-broken-build-script> --output /tmp/hb-out-quiet --quiet
```

**Expected**: no per-line raw subprocess output appears at all — only the
phase start/end banners and, on failure, the same diagnostic block as
Scenario 2, log path included. This is the concise view (SC-003); confirm
the full raw output is still present in the log file even though it never
printed to the console.

## Scenario 3 — Agent repair failure is distinguishable (Story 2)

Using a repository/config that triggers the LLM repair agent (`--agent
claude` or `--agent codex`) against a build that the agent also cannot fix:

```bash
uv run harnessbuddy generate <repo> --agent claude --output /tmp/hb-out-agent
```

**Expected**: two phase pairs appear in order — "Static library build"
(failed) followed by "Agent-assisted library repair" (failed) — and the
final diagnostic's `origin` field/text reads as an agent-repair failure, not
a plain build failure, so the user can tell a repair was attempted and did
not succeed (SC-005).

## Scenario 4 — Debug mode reveals additional detail (Story 3)

Re-run Scenario 2 with `--log-level debug`:

```bash
uv run harnessbuddy generate <repo-with-broken-build-script> --output /tmp/hb-out-debug --log-level debug
```

**Expected**: the same phase banners, live streaming, and diagnostic block
appear as Scenario 2, plus the failing phase's full raw stdout/stderr printed
a second time, inline with the diagnostic — so the user does not need to
scroll back, open the log file, or re-run the tool to see it (SC-004).
Confirm the phase sequence is still readable from stdout despite the extra
volume (Story 3, acceptance scenario 3).

Re-run once more combining `--quiet --log-level debug`:

```bash
uv run harnessbuddy generate <repo-with-broken-build-script> --output /tmp/hb-out-quiet-debug --quiet --log-level debug
```

**Expected**: no live per-line streaming (per `--quiet`), but the diagnostic
block on failure still includes the full raw output inline — confirming
debug mode's inline-with-diagnostic behavior does not depend on the output
having streamed live first.

## Scenario 5 — Non-interactive output stays readable (Edge case)

```bash
uv run harnessbuddy generate <repo-with-broken-build-script> --output /tmp/hb-out-ci > /tmp/hb-ci.log 2>&1
cat /tmp/hb-ci.log
```

**Expected**: the captured file shows the same phase banners and diagnostic
block as Scenario 2, as plain readable text — no control characters, no
information that only appeared via cursor movement in a live terminal
(FR-009).

## Cleanup

```bash
rm -rf /tmp/hb-out-good /tmp/hb-out-bad /tmp/hb-out-quiet /tmp/hb-out-agent \
       /tmp/hb-out-debug /tmp/hb-out-quiet-debug /tmp/hb-out-ci /tmp/hb-ci.log
```

(`.harnessbuddy/` project state directories created under the repo root by
these runs can be removed the same way once logs have been inspected.)
