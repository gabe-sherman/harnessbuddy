# Implementation Plan: Clear Build Logging and Diagnostics

**Branch**: `012-clear-build-logging` | **Date**: 2026-07-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-clear-build-logging/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

`harnessbuddy generate` currently prints raw, unlabeled subprocess output as it
streams (via `run_command_streaming` in `core/subprocesses.py`) and reports
failures as ad hoc `print()` calls in `cli.py` built from each module's own
flat exception type, with no shared notion of "which phase" or "which step"
failed and no way to see extra detail without re-running the tool. This plan
introduces a small, dedicated phase-reporting layer used at each existing
pipeline boundary in `cli.py` (ingestion, static analysis, static library
build, agent-assisted library repair, harness compile probe, agent-assisted
harness repair, output generation): it brackets each phase's existing live
subprocess streaming (unchanged from today) with a visually distinct
start/end banner, always persists each phase's full raw output to a per-run
log file under the existing `.harnessbuddy/<project>/` state directory, and —
on failure — builds a `FailureDiagnostic` from the same
`succeeded`/`stdout`/`stderr`/`exit_code` fields already present on
`BuildExplorationResult`/`HarnessExplorationResult` so the diagnostic and the
authoritative pass/fail check (`check_local_build.sh`/`check_docker_build.sh`)
can never disagree. A new `--quiet` flag lets a user opt into suppressing the
per-line streaming (banners and diagnostics stay visible either way) — this
is a reversal of this plan's original design, which defaulted to a condensed
view and made streaming opt-in via debug mode; that direction changed based
on explicit user feedback that live output should remain the default and
`--quiet` should be the opt-in (see research.md Decision 5, revised). The
existing `--log-level` flag is kept (not replaced) so it remains available
for future logging extensibility; its `debug` choice, independent of
`--quiet`, includes the failing phase's raw output inline with its
diagnostic and sets Python's internal `logging` level to `DEBUG`, while its
other choices continue to control only logging verbosity as today.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: Python standard library only (`logging`,
`dataclasses`, `contextlib`, `enum`) — no new third-party dependency (see
research.md for the reuse-vs-add-a-library decision)

**Storage**: Files — one raw-output log per phase per run, under the existing
`.harnessbuddy/<project>/` state directory (`core/paths.py`)

**Testing**: `pytest`, exercising the new phase-reporting/diagnostic module at
the unit level (mocking only the genuine boundaries: clock and filesystem),
plus updates to `tests/test_cli.py` asserting the new console output surfaces
and to `tests/library_builder/test_library_build.py` asserting phase logs are
written during a real, unmocked build

**Target Platform**: Local developer host CLI (macOS/Linux) and the
OSS-Fuzz Docker probe image (verification output must look the same shape in
both, since both `LocalExecutor` and `OssFuzzExecutor` drive the same
console)

**Project Type**: Single project — `src/harnessbuddy/`, `tests/`

**Performance Goals**: Phase reporting/log-file writing MUST add no
observable overhead to a build (well under 1 second across a full run);
this is a console-output change, not a build-performance change

**Constraints**: MUST NOT depend on interactive-terminal-only features
(spinners, cursor control) since output must stay readable when redirected
to a file or CI log (FR-009); MUST NOT change build, verification, or
generation logic itself (only how their existing results are reported);
MUST NOT introduce a new exception hierarchy — diagnostics are built from the
existing result dataclasses and exception types in `library_builder/models.py`
and friends; MUST NOT change `--skip-validation`'s existing continue-vs-stop
semantics (including for the agent stop-for-human/budget-limited path added
in `9622ce2`, after this plan's initial draft — see research.md addendum) —
only how each outcome, continuing or stopping, is reported

**Scale/Scope**: A fixed, small set of pipeline phases (~7) per `generate`
run; one run at a time; no concurrency or multi-run aggregation in scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Code Quality**: No hard-limit risk identified — the new phase-reporting
  module is additive and small (a `Phase` enum, a reporter/context-manager, a
  `FailureDiagnostic` dataclass); existing 100-line/complexity-8/5-param
  limits apply as normal. Zero-warnings gates (`ruff format`, `ruff check`,
  `ty check`) apply unchanged. **PASS**.
- **II. Testing Standards**: This feature does not touch build-system
  detection, verification, or executor *logic* — only how their existing
  results are surfaced — so `tests/library_builder/test_library_build.py` and
  `tests/run_ground_truth.py` continue to exercise real, unmocked builds and
  MUST keep passing unchanged in behavior (only their output-observing
  assertions may be extended). New tests for the reporting layer mock only
  genuine boundaries (clock, filesystem), per the mocking rule. Because this
  touches the console-output path that wraps every executor call, the
  ground-truth suite (`tests/run_ground_truth.py`) will still be run once
  implementation is complete, as a confirmation rather than because the
  build logic changed. **PASS**.
- **III. User Experience Consistency**: This feature directly implements the
  "what failed / which input / what to do next" error-message rule — the
  `FailureDiagnostic` is the concretization of that rule for phase failures.
  The diagnostic is derived from, not a re-derivation of, the
  `check_local_build.sh`/`check_docker_build.sh` pass/fail result, preserving
  "single definition of the build passed." **PASS**.
- **IV. Modularity**: Phase reporting and diagnostic formatting get their own
  module rather than more ad hoc `print()` calls spread through `cli.py`,
  keeping `cli.py` as dispatch-only. Deterministic analysis/build/generation
  code is not modified to know about console formatting; they continue to
  return the same result dataclasses, and the new module only reads those
  results. **PASS**.
- **V. Extensibility**: The phase list is a single ordered enum/registry;
  adding a future build system or output environment extends that registry
  rather than requiring a redesign. Keeping the existing `--log-level` flag
  (rather than adding a new, narrower `--debug` boolean) is a deliberate
  extensibility choice: its unused `info`/`warning`/`error`/`critical` tiers
  stay available for future logging work instead of being discarded in favor
  of a flag that would need replacing again later. This isn't new speculative
  surface — the flag already exists — so it doesn't conflict with "no
  configuration ahead of a concrete need." **PASS**.

No violations identified. Complexity Tracking table is not needed.

**Post-Phase-1 re-check**: `research.md`, `data-model.md`, and
`contracts/cli-console-contract.md` introduce no new dependency, no new
exception hierarchy, and no change to build/verification/generation logic —
only a new, single-responsibility console-reporting module that reads
existing result types. All five gates above still **PASS** unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/012-clear-build-logging/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/harnessbuddy/
  cli.py                              # updated: call the reporter at each existing
                                       # phase boundary instead of ad hoc print();
                                       # new --quiet flag on the generate parser
  core/
    subprocesses.py                   # updated: run_command_streaming always logs
                                       # to file; live per-line printing stays on
                                       # by default and is gated off only by
                                       # --quiet (not by --log-level)
    reporting.py                      # NEW: Phase enum, PhaseReporter context
                                       # manager, FailureDiagnostic dataclass,
                                       # console formatting (phase banners,
                                       # diagnostic summary)
  library_builder/
    models.py                         # unchanged: existing result dataclasses are
                                       # read by reporting.py, not modified
    agents.py                         # updated: report agent-repair phase start/
                                       # end through reporting.py instead of its
                                       # own print()/banner strings
    exploration.py                    # unchanged (build logic)
    harness_explorer.py                # unchanged (build logic)

tests/
  test_reporting.py                   # NEW: unit tests for Phase/PhaseReporter/
                                       # FailureDiagnostic
  test_cli.py                         # updated: assert new console output
                                       # surfaces (phase banners, diagnostics,
                                       # --quiet, and --log-level debug
                                       # behavior, including combinations)
  library_builder/
    test_library_build.py             # updated: assert a per-phase log file is
                                       # written during a real, unmocked build
```

**Structure Decision**: Single project, matching the existing
`src/harnessbuddy/` / `tests/` layout described in `CLAUDE.md`'s source map.
The feature adds one new module (`core/reporting.py`) rather than a new
subpackage, since its responsibility (formatting phase/failure output for the
console) is a single cohesive concern, not a family of related modules.

## Complexity Tracking

*No entries — Constitution Check passed with no violations.*
