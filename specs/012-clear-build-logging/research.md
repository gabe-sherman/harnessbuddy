# Research: Clear Build Logging and Diagnostics

## Context recap (current state, for reference)

- Output today is almost entirely `print()` (44 call sites), concentrated in
  `cli.py`. `run_command_streaming` (`core/subprocesses.py:24`) prints every
  subprocess line live as it runs; `run_agent_streaming`
  (`core/agent_stream.py:212`) does the same for the LLM repair agent's
  streamed events.
- `--log-level` (`cli.py:112`) exists but is effectively dead: it configures
  `logging.basicConfig`, but only 3 files ever call `logger.debug(...)`
  (`analysis.py`, `harness_explorer.py`, `environments/oss_fuzz.py`), and the
  default level is silent.
- Failures are reported ad hoc per call site in `cli.py` (e.g.
  `_handle_library_build_failure`), reading a flat, per-module exception
  (`BuildFailureError`, `UnsupportedRepositoryError`,
  `EnvironmentUnavailableError`, `OutputDirectoryExistsError`, ...) or a
  `succeeded: bool` field on `BuildExplorationResult`/`HarnessExplorationResult`
  (`models.py`), which also carry `stdout`/`stderr`/`exit_code`/
  `duration_seconds`/`environment`.
- The only signal today distinguishing "agent repair" from a plain
  deterministic build is two `print()` banner lines in `cli.py` before
  invoking the agent, and a fixed `"=== Agent Run Summary ==="` trailer
  afterward (`agents.py:180`, `agent_stream.py:270`).
- The pipeline's phases, in order, are: environment availability check →
  repo ingestion → static analysis → output path resolution → library build
  (deterministic, then agent repair on failure) → harness compile probe
  (deterministic, then agent repair on failure) → output generation → stats
  persistence.

## Decision 1: Console formatting approach

**Decision**: Use the Python standard library only (`print`, `logging`,
plain-text formatting) for phase banners and diagnostics. Do not add a
third-party terminal UI library (e.g. `rich`, `tqdm`).

**Rationale**: CLAUDE.md's "justify new dependencies" principle requires a
concrete need the standard library can't meet — there isn't one here: phase
banners and a failure summary are a handful of formatted `print()` calls.
FR-009 additionally requires output to stay fully readable when redirected to
a file or CI log, which favors plain, unstyled text over a library whose main
value (live-updating panels, spinners, color) depends on an interactive
terminal. The OSS-Fuzz Docker probe image is a second runtime this output
must render correctly in; not adding a dependency there is one less thing to
provision or fail to import.

**Alternatives considered**:
- `rich` — nice-looking live phase panels, but its main value degrades to
  plain text in a redirected/CI context anyway (so it doesn't add much for
  FR-009), and it's a new dependency with no other consumer in the codebase.
- `click` echo/progress bar — the CLI already uses `argparse`; adopting
  `click` for output alone would mean a partial framework migration for a
  logging-only feature, which is out of scope.
- Raw `print()`, extended — consistent with the current 44-call-site
  convention and adds no dependency; chosen.

## Decision 2: Debug flag design

**Decision**: Keep the existing `--log-level` flag (`debug|info|warning|
error|critical`, currently silent by default) rather than adding a new,
narrower `--debug` boolean. Its `debug` choice specifically (a) includes each
failing phase's full raw output inline with its diagnostic, in addition to
whatever already streamed live by default or was suppressed by `--quiet`, and
(b) sets the internal `logging` level to `DEBUG` so the existing (currently
orphaned) `logger.debug(...)` call sites in `analysis.py`,
`harness_explorer.py`, and `environments/oss_fuzz.py` become visible. The
other four choices continue to control only Python's internal logging
verbosity, as today (currently narrow in effect, but preserved as a hook for
future logging work rather than discarded).

**Revised (2026-07-14, alongside Decision 5)**: since live streaming is now
the default (not something `debug` turns on), `--log-level debug` no longer
has a "re-enable streaming" role — that role is gone entirely, since there is
nothing to re-enable. `--log-level` and the new `--quiet` flag are
orthogonal: `--quiet` controls only whether per-line output streams live;
`--log-level debug` controls only (a) the inline-with-diagnostic repeat of a
failing step's raw output and (b) Python's internal logging level. Both,
either, or neither may be set in the same run.

**Rationale**: The user explicitly requested keeping `--log-level` instead
of introducing a single boolean, specifically to leave room for future
logging extensibility (e.g. giving `info`/`warning` meaning later) without
having to reintroduce a multi-valued flag after having collapsed it to a
boolean. From the CLI user's perspective this still behaves as the spec's
"single on/off toggle" for Story 3's behavior — one choice (`debug`) turns
the additional detail on, the rest don't — so it satisfies FR-008 without
contradicting the spec's Assumptions section, even though the underlying
flag has room for more values than that one on/off distinction. This also
reads better against "replace, don't deprecate": `--log-level` already
exists in `cli.py`; wiring its `debug` choice to do something meaningful
(today it barely does anything — only 3 files call `logger.debug`) revives
existing dead-ish code rather than adding a second, overlapping flag that
would eventually make one of the two redundant.

**Alternatives considered**:
- New, separate `--debug` boolean flag replacing `--log-level` — the
  original plan; rejected per explicit user direction to preserve
  `--log-level`'s room for future tiers instead of narrowing the CLI surface
  to a boolean now.
- `-v`/`-vv` counted verbosity in addition to `--log-level` — rejected,
  redundant with the levels `--log-level` already offers.

## Decision 3: Where raw output is preserved

**Decision**: Each phase that runs subprocesses writes its full raw
stdout/stderr to a dedicated log file at
`.harnessbuddy/<project>/logs/<phase-name>.log`, using the existing
`core/paths.py` project-state-directory convention
(`project_dir(state_dir, project_name)`). A `FailureDiagnostic` for a failed
phase always includes that file's path.

**Rationale**: FR-004 requires raw output to be retrievable after the run
ends, including after the terminal/process is gone — output held only in
memory (e.g. only in `BuildExplorationResult.stdout`) doesn't survive that.
`.harnessbuddy/<project>/` already exists as the place per-run artifacts
(cloned source, `state.json`) live, so a `logs/` subdirectory there is
consistent with an established convention rather than a new one. Writing one
file per phase (rather than one combined run log) makes FR-005's "where to
find the complete raw output for that failure" precise — the diagnostic can
name the exact file for the phase that failed, not a byte offset into a
combined transcript.

**Alternatives considered**:
- Combined single `run.log` for the whole invocation — rejected, makes
  pointing at "this phase's output" imprecise and requires the reader to
  search rather than open a name that matches the failed phase.
- No persistence, rely on debug mode alone to see output — rejected, directly
  contradicts FR-004's "even when that raw output is not printed to the
  console by default."

## Decision 4: Data shape for phases and diagnostics

**Decision**: Add a `Phase` enum (`INGESTION`, `STATIC_ANALYSIS`,
`STATIC_LIBRARY_BUILD`, `AGENT_LIBRARY_REPAIR`, `HARNESS_COMPILE_PROBE`,
`AGENT_HARNESS_REPAIR`, `OUTPUT_GENERATION`) and a `FailureDiagnostic`
dataclass built by reading the existing `succeeded`/`stdout`/`stderr`/
`exit_code`/`environment` fields already on `BuildExplorationResult` /
`HarnessExplorationResult` (`models.py`) and the existing exception types
(`BuildFailureError`, `UnsupportedRepositoryError`,
`EnvironmentUnavailableError`, `OutputDirectoryExistsError`, etc.), rather
than introducing a new, parallel exception hierarchy.

**Rationale**: Constitution Principle IV (Modularity) and "no premature
abstraction" both argue against a wide refactor of five existing, tested
exception types just to add a console-formatting feature. The result
dataclasses already carry everything a diagnostic needs (pass/fail, raw
output, exit code, which environment); a diagnostic builder that reads them
is strictly additive. This also structurally guarantees Principle III's
"single definition of the build passed" — the diagnostic can't disagree with
`check_local_build.sh`/`check_docker_build.sh` because it's derived from the
same `succeeded` field those scripts set.

**Alternatives considered**:
- New shared exception base class (`HarnessBuddyError`) that all modules
  raise — rejected as unnecessary scope expansion for this feature; existing
  exceptions already carry enough information (message, and for agent errors,
  `.summary`/`.report`) for a diagnostic builder to consume via `isinstance`
  checks, without changing what any module raises.
- Encode phase identity as a plain string instead of an enum — rejected,
  Principle V (Extensibility) and "use enums for state ... rather than
  stringly typed sentinels" (python_code_standards.md) both call for an enum
  here.

**Addendum (2026-07-14, alongside Decision 5's revision): banner visual
style.** Now that phase banners bracket live-streaming raw output rather
than standing alone, they need to be visually distinct enough to spot while
scrolling past build-tool chatter — not just textually different. Deterministic
phases reuse the existing `"=" * 25 + label + "=" * 25` convention already
used for the agent-output banner in `build_library()`/`build_harness()`;
agent-assisted phases (`AGENT_LIBRARY_REPAIR`, `AGENT_HARNESS_REPAIR`) use a
different fill character (e.g. `"#"`) plus an explicit "AGENT:" prefix, so
FR-002's "visually and textually distinguish" is satisfied by two
independent signals (character choice and wording), not wording alone.

## Decision 5 (revised 2026-07-14, post-user-feedback): Default vs. quiet
console volume

**Original decision (superseded)**: By default, each phase would print
exactly one start line and one end line, with `run_command_streaming`'s
per-line printing suppressed by default and only re-enabled via
`--log-level debug`.

**Why it was superseded**: explicit user feedback after reviewing this plan:
they want to keep seeing live build output by default — the actual
complaint was never "too much output," it was "I can't tell what phase
produced this output, or whether the run is stuck." Suppressing output by
default over-corrected past the real problem.

**Revised decision**: By default, `run_command_streaming` keeps printing
every line live exactly as it does today. `PhaseReporter` brackets that
stream with a start line and an end line (success/failure) per phase, using
a separator distinctive enough (reusing/extending the existing
`"=" * 25 + "..."` convention already used for agent-output banners, with a
visually distinct fill character for agent-assisted phases — see Decision 4
addendum below) that a phase boundary is never mistaken for build-tool output
or missed while scrolling. A new `--quiet` flag (independent of
`--log-level`) suppresses the per-line streaming only; phase banners and
failure diagnostics are unaffected by it. On failure, the console always
shows the `FailureDiagnostic` (phase, step, message, log file path)
regardless of `--quiet`; `--log-level debug` additionally inlines the raw
output directly with that diagnostic (Decision 2, revised).

**Rationale**: This directly implements the revised FR-003 (clear phase
boundaries around full default streaming) and FR-011 (opt-in `--quiet` is
now the mechanism for SC-003's volume reduction), matches the user's stated
preference, and still keeps `run_command_streaming` centralized — it just
gates its live-printing on `--quiet` instead of on `--log-level debug`. It
also resolves, for free, the "does a long phase look hung" concern raised
during review of the original decision: default streaming itself proves the
run is alive; `--quiet` users explicitly accept that trade-off (same
convention as `curl -s`/`apt -q`), so no separate heartbeat mechanism is
needed.

**Alternatives considered**:
- Keep the original "concise by default" design — rejected per direct user
  feedback; see above.
- Add a periodic heartbeat line during quiet, long-running phases (e.g.
  "still running, Ns elapsed") — considered as a mitigation for `--quiet`'s
  silence trade-off, but rejected for now as unrequested scope; `--quiet` is
  an explicit, informed opt-in, not a default a hung-looking run could
  surprise a user with.
- Always print full raw output on any failure regardless of mode — rejected,
  duplicates output the user already saw stream live in default mode with no
  added value there; debug mode's inline-with-diagnostic behavior is the
  right place for that convenience since it targets the "avoid scrolling
  back" need specifically.

## Open questions

None — no `[NEEDS CLARIFICATION]` markers remain in scope; all Technical
Context fields above are resolved.

## Addendum (2026-07-14, post-tasks.md): `--skip-validation` now gates the
agent stop-for-human/budget-limited path too

Commit `9622ce2` ("Update final output generation and patch a few gaps")
landed in `cli.py` after this feature's `tasks.md` was finalized (08:25) and
changed a control-flow assumption this feature's diagnostic work depends on:

- Previously, an agent repair raising `BuildFailureError`/`LLMBudgetError`
  ("stop for human action" or LLM budget exhausted) always ended the run
  immediately (`return 1`), regardless of `--skip-validation`.
- Now, `_run_library_phase_or_agent_error`/`_run_harness_phase_or_agent_error`
  catch that exception and hand it to `_handle_library_agent_error`/
  `_handle_harness_agent_error`, which — under `--skip-validation` — convert it
  into a synthetic failed `BuildExplorationResult`/`HarnessExplorationResult`
  (`_build_result_from_agent_error`/`_harness_result_from_agent_error`) and let
  the pipeline continue to the next phase, exactly like a deterministic
  failure does today.

This has two consequences for this feature's scope:

1. **FR-007 ordering now spans phases via a second path.** Before this
   change, "two phases fail in sequence" (spec Acceptance Scenario 4) could
   only happen as static-build-fails-then-its-own-repair-also-fails. Now it
   can also happen as library-phase-fails-via-agent-stop-for-human (continued
   past by `--skip-validation`) followed by a harness-phase failure — a
   cross-phase chain, not just a within-phase one. `RunReport.diagnostics`
   (data-model.md) and the ordering task (`harnessbuddy-zo1`) must cover this
   case too, not only the original within-library-phase chain.
2. **A concrete, pre-existing duplicate-print bug for the diagnostic builder
   to fix, not preserve.** Under `--skip-validation`, when the library-build
   agent raises `BuildFailureError`/`LLMBudgetError`,
   `_handle_library_agent_error` already prints via
   `_print_agent_stop_for_human` (message + `exc.report.summary`), then
   returns the synthetic failed result, which `_report_library_build_result`
   feeds into `_handle_library_build_failure` — which prints its own "Failed
   to produce valid build ... after agent repair." plus
   `result.agent_summary` (the *same* summary) a second time. The equivalent
   harness-side path (`_handle_harness_agent_error`) does not have this
   duplicate, since there is no analogous downstream harness-failure-report
   call site. The new diagnostic builder/formatter (T016/T017,
   `harnessbuddy-vi5`/`harnessbuddy-9qe`) must produce exactly one diagnostic
   block per failed phase; `harnessbuddy-mwq` (replacing ad hoc prints) is the
   task responsible for collapsing this specific double-print rather than
   reproducing it behind the new formatter.

No change to `Phase`, `FailureDiagnostic`, or `RunReport`'s shape is needed —
this is a control-flow fact about *when* diagnostics fire and *how many*
call sites currently print for one failure, not a new data shape.
