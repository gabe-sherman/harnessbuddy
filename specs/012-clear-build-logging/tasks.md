---

description: "Task list for Clear Build Logging and Diagnostics"
---

# Tasks: Clear Build Logging and Diagnostics

**Input**: Design documents from `/specs/012-clear-build-logging/`

**Prerequisites**: plan.md, spec.md, data-model.md, contracts/cli-console-contract.md, research.md

**Tracking**: Per this project's rules (`CLAUDE.md`), every task below is tracked as a
**beads issue**, not just a markdown checkbox. This file is a readable index into that
tracker — `bd` is the source of truth for status. Use `bd show <id>`, `bd ready`,
`bd update <id> --claim`, and `bd close <id>` to work these tasks; do not track progress
by editing the checkboxes here.

**Tests**: Test tasks are included throughout (constitution Testing Standards treats
this as required for this project, not optional), mocking only genuine boundaries
(clock, filesystem) per that principle.

**Organization**: Tasks are grouped by user story (from `spec.md`) to enable
independent implementation and testing of each story.

## Format: `[ID] [P?] [Story?] Description (bead: <id>)`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- **(bead: ...)**: The `bd` issue ID tracking this task — dependencies are wired there,
  not just implied by list order

## Phase 1: Setup

**Purpose**: Scaffold the new module and its test file (no logic yet)

- [ ] T001 [P] Create `src/harnessbuddy/core/reporting.py` module scaffold (bead: harnessbuddy-1u7)
- [ ] T002 [P] Create `tests/test_reporting.py` scaffold (bead: harnessbuddy-p6e)

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared `Phase`/`PhaseExecution`/`RunReport` scaffolding every user story builds on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 Implement `Phase` enum in `src/harnessbuddy/core/reporting.py` (bead: harnessbuddy-zl9) — depends on T001
- [ ] T004 Implement `PhaseExecution` dataclass with transition methods in `src/harnessbuddy/core/reporting.py` (bead: harnessbuddy-71w) — depends on T003
- [ ] T005 Implement `RunReport` aggregator in `src/harnessbuddy/core/reporting.py` (bead: harnessbuddy-7tb) — depends on T004
- [ ] T006 [P] Unit tests for `Phase`/`PhaseExecution`/`RunReport` in `tests/test_reporting.py` (bead: harnessbuddy-5jq) — depends on T005, T002

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 - Always know the current phase (Priority: P1) 🎯 MVP

**Goal**: Every phase boundary announces itself with a visually distinct banner bracketing
that phase's live raw output (default, unchanged from today); `--quiet` lets a user opt
into suppressing the live per-line output while keeping banners; raw output is always
preserved in a per-phase log file regardless of mode.

**Independent Test**: Run `harnessbuddy generate` against a repository that completes
successfully; confirm that scanning stdout for phase start/end banners alone (without
needing to read or parse the streamed build-tool output itself) names every phase the run
passed through, in order — both with and without `--quiet`.

- [ ] T007 [US1] Implement `PhaseReporter` context manager in `src/harnessbuddy/core/reporting.py`, with a start/end banner style distinctive enough to stand out from raw output streaming between them, and a visually distinct fill character/prefix for agent-assisted phases vs. deterministic ones (research.md Decision 4 addendum) (bead: harnessbuddy-6wf) — depends on T004
- [ ] T008 [US1] Add per-phase log file writing to `run_command_streaming` in `src/harnessbuddy/core/subprocesses.py` — always persists full raw stdout/stderr regardless of `--quiet` (bead: harnessbuddy-2e1)
- [ ] T008a [P] [US1] Add a `--quiet` flag to the `generate` argparse parser in `src/harnessbuddy/cli.py` (bead: harnessbuddy-yy1)
- [ ] T008b [US1] Gate `run_command_streaming`'s live per-line printing on `--quiet` in `src/harnessbuddy/core/subprocesses.py` — default (flag absent) keeps printing live exactly as today; `--quiet` suppresses it. Repurposed from the original `harnessbuddy-5ix` ("re-enable full streaming in debug mode"), which is obsolete now that streaming is the default rather than a debug-gated behavior (bead: harnessbuddy-5ix) — depends on T008, T008a
- [ ] T009 [P] [US1] Add `project_logs_dir` helper to `src/harnessbuddy/core/paths.py` (bead: harnessbuddy-3go)
- [ ] T010 [US1] Wire `PhaseReporter` into `src/harnessbuddy/cli.py`'s `generate` pipeline at all 7 phase boundaries, threading the `--quiet` bool through to each phase's subprocess calls (bead: harnessbuddy-n7t) — depends on T007, T008, T008a, T008b, T009
- [ ] T011 [US1] Route agent invocation banners through `PhaseReporter` in `src/harnessbuddy/library_builder/agents.py`, using the agent-distinct banner style from T007 (bead: harnessbuddy-kgj) — depends on T010
- [ ] T012 [P] [US1] Unit tests for `PhaseReporter` banners in `tests/test_reporting.py`, including a banner-distinctness assertion between deterministic and agent-assisted phases (bead: harnessbuddy-4e5) — depends on T007
- [ ] T013 [US1] Update `tests/test_cli.py` to assert (a) the full ordered phase banner sequence plus live per-line raw output by default, and (b) with `--quiet`, the same banner sequence with no per-line raw output (bead: harnessbuddy-r61) — depends on T010, T011, T008b
- [ ] T014 [US1] Update `tests/library_builder/test_library_build.py` to assert a real build writes its phase log file (bead: harnessbuddy-99y) — depends on T008, T009

**Checkpoint**: User Story 1 is fully functional and independently testable (MVP)

---

## Phase 4: User Story 2 - Get a clear diagnostic when something fails (Priority: P2)

**Goal**: Failures produce a concise diagnostic naming the phase, step, message, origin
(deterministic vs. agent), and log location — never a raw output dump.

**Independent Test**: Run `harnessbuddy generate` against a repository crafted to fail
at a known phase; confirm the diagnostic names the phase/step and points to the log,
without any special flag required.

- [ ] T015 [US2] Implement `FailureDiagnostic` dataclass in `src/harnessbuddy/core/reporting.py` (bead: harnessbuddy-vlf) — depends on T004
- [ ] T016 [US2] Implement diagnostic builder functions in `src/harnessbuddy/core/reporting.py` (bead: harnessbuddy-vi5) — depends on T015
- [ ] T017 [US2] Implement diagnostic console formatting in `src/harnessbuddy/core/reporting.py` (bead: harnessbuddy-9qe) — depends on T015
- [ ] T018 [US2] Replace ad hoc failure prints in `src/harnessbuddy/cli.py` with the diagnostic builder/formatter, including the newer (post-tasks.md, commit `9622ce2`) `_handle_library_agent_error`/`_handle_harness_agent_error`/`_build_result_from_agent_error`/`_harness_result_from_agent_error` call sites, and collapsing the existing duplicate print of the same agent summary that occurs today when a library-build agent's stop-for-human error is converted into a synthetic result under `--skip-validation` (see research.md addendum) (bead: harnessbuddy-mwq) — depends on T016, T017, T010
- [ ] T019 [US2] Preserve multi-failure ordering across a run, including the cross-phase case `--skip-validation` now enables (library phase fails via agent stop-for-human, run continues, harness phase separately fails), not only the original within-library-phase static-build-then-repair chain (bead: harnessbuddy-zo1) — depends on T018
- [ ] T020 [US2] Handle pre-phase startup failures in `src/harnessbuddy/cli.py`, including `_check_environment_availability`'s existing ad hoc print (the concrete pre-`INGESTION`-phase failure site today) (bead: harnessbuddy-dbp) — depends on T017
- [ ] T021 [P] [US2] Unit tests for diagnostic builder functions in `tests/test_reporting.py` (bead: harnessbuddy-xfv) — depends on T016
- [ ] T022 [US2] Update `tests/test_cli.py` for diagnostic content, ordering, and startup failures (bead: harnessbuddy-wl6) — depends on T018, T019, T020

**Checkpoint**: User Stories 1 and 2 both work independently

---

## Phase 5: User Story 3 - Opt into deeper diagnostic detail (Priority: P3)

**Goal**: The existing `--log-level debug` choice (not a new flag — kept per explicit
project direction, see `research.md` Decision 2) inlines a failing phase's full raw output
directly with its diagnostic and sets Python's internal logging to `DEBUG`, independent of
whether `--quiet` (US1) is also set — on top of, never instead of, the default
phase/diagnostic output.

**Independent Test**: Run `harnessbuddy generate` twice against the same failing
repository — once at the default log level and once with `--log-level debug`, each also
repeated with `--quiet` — and confirm the debug run always adds the inline raw-output
detail without losing phase-sequence readability, whether or not `--quiet` suppressed live
streaming.

- [ ] T023 [US3] Thread a debug boolean derived from `--log-level` into reporter/diagnostic call sites in `src/harnessbuddy/cli.py` (bead: harnessbuddy-7xl) — depends on T010, T018
- ~~T024~~ retired — its scope ("re-enable full streaming in debug mode") moved to T008b in User Story 1, since streaming is now the default rather than a debug-gated behavior; see Notes below.
- [ ] T025 [US3] Include full raw output inline in the diagnostic in debug mode, regardless of whether `--quiet` is also set (bead: harnessbuddy-38g) — depends on T017, T023
- [ ] T026 [US3] Confirm internal `logging` level is set to `DEBUG` by `--log-level debug`, and that no separate `--debug` flag exists (bead: harnessbuddy-5xe) — depends on T023
- [ ] T027 [P] [US3] Unit tests for debug mode's inline-diagnostic behavior in `tests/test_reporting.py`, confirming it fires the same way whether or not `--quiet` is set (bead: harnessbuddy-oxo) — depends on T025
- [ ] T028 [US3] Update `tests/test_cli.py` for `--log-level debug` end-to-end behavior, including the `--quiet --log-level debug` combination (bead: harnessbuddy-91e) — depends on T025, T026, T013

**Checkpoint**: All three user stories are independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T029 [P] Run `ruff format`/`ruff check`/`ty check` across all changed files and fix any warnings (bead: harnessbuddy-3rb) — depends on T008b, T011, T019, T026
- [ ] T030 Run `uv run pytest -q` (smoke subset) (bead: harnessbuddy-c1b) — depends on T029
- [ ] T031 Run `uv run python tests/run_ground_truth.py` (requires Docker), per constitution Testing Standards (bead: harnessbuddy-akj) — depends on T030
- [ ] T032 [P] Execute the `quickstart.md` validation scenarios manually (bead: harnessbuddy-er5) — depends on T030
- [ ] T033 Update `CLAUDE.md`'s Source Map for the new `core/reporting.py` module (bead: harnessbuddy-8sq) — depends on T029

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — T001, T002 can start immediately, in parallel.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational only. No dependency on US2/US3. Now
  includes the `--quiet` flag (T008a) and the streaming-gate behind it (T008b) — this is
  where console-volume control now lives, not User Story 3.
- **User Story 2 (Phase 4)**: Depends on Foundational (T004→T015) and on US1's `PhaseReporter`
  wiring (T010→T018), since diagnostics print at the same phase boundaries US1 wires up.
- **User Story 3 (Phase 5)**: Depends on US1 (T010, T018) and US2 (T018), for the reporter
  and diagnostic call sites debug mode's inline-raw-output behavior extends. No longer
  depends on gating streaming — that's US1's `--quiet` now, and debug mode doesn't touch it.
- **Polish (Phase 6)**: Depends on all three stories' implementation tasks completing.

### Parallel Opportunities

- T001/T002 (Setup) in parallel.
- T009 (`project_logs_dir`) in parallel with T007 (`PhaseReporter`), T008 (log writing), and
  T008a (`--quiet` flag).
- T012, T021, T027 (unit test tasks) in parallel with sibling tasks in the same story once
  their single dependency lands.
- T029 and T032 in Polish can run in parallel once T030 lands (T032 does not depend on T031).

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Setup) and Phase 2 (Foundational).
2. Complete Phase 3 (User Story 1) — `bd ready` will surface T001/T002 first, then the rest
   as dependencies clear.
3. **STOP and VALIDATE**: run Scenario 1 from `quickstart.md` against a real successful build.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. User Story 1 → validate independently (Scenario 1, plus Scenario 2b for `--quiet`) → MVP.
3. User Story 2 → validate independently (Scenarios 2–3).
4. User Story 3 → validate independently (Scenario 4, including its `--quiet --log-level debug` case), plus Scenario 5 (non-interactive output).
5. Polish (T029–T033), finishing with the ground-truth check required by the constitution.

## Notes

- **Design reversal (2026-07-14, after user review of this plan)**: the original default
  was concise-by-default with `--log-level debug` re-enabling full streaming. Based on
  direct user feedback, this flipped: full live streaming is now the default (unchanged
  from today's behavior), bracketed by distinctive phase banners, and a new `--quiet` flag
  (T008a/T008b, User Story 1) is the opt-in for the concise view. `--log-level debug`
  (User Story 3, T023/T025/T026) no longer controls streaming at all — it only inlines a
  failing phase's raw output with its diagnostic and sets Python's logging level, both
  independent of `--quiet`. See `research.md` Decision 5 (revised) and Decision 2 (revised),
  and `spec.md`'s Assumptions section for the full rationale. `harnessbuddy-5ix` (originally
  "re-enable full streaming in debug mode", User Story 3) was repurposed rather than
  discarded — it's now T008b, "gate streaming behind `--quiet`", in User Story 1.
- No task was generated for a new `--debug` flag — the user explicitly directed keeping
  `--log-level` for future logging extensibility (see `research.md` Decision 2); T023,
  T025, T026 implement debug behavior as a derived boolean from `--log-level debug` instead.
- `cli.py` gained new control flow (`_run_library_phase_or_agent_error`,
  `_run_harness_phase_or_agent_error`, `_handle_library_agent_error`,
  `_handle_harness_agent_error`) after this file was first finalized (commit `9622ce2`) —
  `--skip-validation` now also converts an agent's stop-for-human/budget-limited error into
  a synthetic failed result instead of always stopping the run. T018/T019/T020 above were
  updated to account for this; see `research.md`'s addendum for the full explanation,
  including a pre-existing duplicate-print bug T018 must resolve rather than preserve.
- Verify tests fail before implementing where a test task precedes no corresponding
  implementation task in the same story (e.g. T012 depends only on T007, not on T010–T011).
- Use `bd ready` to see which of these are currently unblocked; use `bd show <id>` for the
  full description/acceptance criteria recorded on each issue.
