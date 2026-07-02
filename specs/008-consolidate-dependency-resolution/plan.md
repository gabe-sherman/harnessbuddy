# Implementation Plan: Consolidate Library Dependency Resolution

**Branch**: `main` (no feature branch created — no `before_specify`/`before_plan` hook
registered in this project) | **Date**: 2026-07-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-consolidate-dependency-resolution/spec.md`

## Summary

`cli.py` currently owns persisted dependency state (`_ProjectState`, `load_project_state`,
`save_project_state`) and a merge function (`merge_packages_into_state`) called from 5 near-
identical sites, plus inlined deterministic-path translation logic in `_run_harness_phase`.
This plan moves all of it into a new `library_builder/dependency_resolution.py` module built
around one shared type (`LibraryDependency`), a closed `DependencySource` enum (replacing
free-text tags), and one merge function — reducing `cli.py` back to orchestration/dispatch only,
per Constitution Principle II. `models.py` (`AgentReport`, `BuildExplorationResult`,
`HarnessExplorationResult`), `agents.py`, `harness_explorer.py`, `package_names.py`, and both
`SKILL.md` files are untouched: this is a pure internal consolidation with no change to any
already-correct external contract or user-visible behavior (spec's own User Story 3 / FR-004).

## Technical Context

**Language/Version**: Python 3.13 (`uv venv`)

**Primary Dependencies**: none new — stdlib `dataclasses`/`enum`/`json`, plus the existing
`harnessbuddy.library_builder.package_names` module this refactor calls into rather than
replaces

**Storage**: `.harnessbuddy/<project>/state.json` — same on-disk JSON shape, relocated
ownership (`dependency_resolution.py` instead of `cli.py`)

**Testing**: `pytest`, with new direct unit tests for `dependency_resolution.py` (no subprocess
mocking needed — pure functions/dataclasses) alongside the existing CLI-level integration tests

**Target Platform**: Same as specs/005/007 — author's dev machine (Linux/macOS) for exploration,
Debian/Ubuntu OSS-Fuzz Docker image and local dev machine's own platform for generated output

**Project Type**: Single Python CLI project (`src/harnessbuddy/`), no new top-level package —
this module lives inside the existing `library_builder` tool package

**Performance Goals**: N/A — internal refactor of already-fast, subprocess-mocked-in-tests code

**Constraints**: Zero new runtime dependencies; zero change to `state.json`'s on-disk format;
zero change to `agent_report.json`'s wire format (see `research.md`'s correlation-gap decision)

**Scale/Scope**: One new module (`dependency_resolution.py`, ~4 producer/merge/persistence
functions + 2 types), `cli.py` reduced by the code being moved out, new direct unit tests for
the module, existing CLI-level tests unchanged (their assertions must not need to change — that
*is* the acceptance bar, per User Story 3)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I. Code Quality Is Non-Negotiable | PASS — no new warnings expected; the new module's functions are small and independently unit-testable, easier to keep under the complexity/line limits than the current inlined `cli.py` blocks. |
| II. Modular Package Boundaries | PASS (this feature's core justification) — moves `_ProjectState`/state-persistence/merge logic out of `harnessbuddy.cli` and into `harnessbuddy.library_builder`, directly correcting an existing drift from "`cli.py` MUST stay limited to argument parsing and dispatch." `core/` is untouched (dependency resolution is `library_builder`-specific, not cross-tool, consistent with `package_names.py`/`harness_explorer.py` already living there). |
| III. Extensible Multi-Tool Architecture | PASS — no new tool; `feature_extractor` untouched; this only makes the *existing* tool's internals easier to extend, per User Story 1. |
| IV. Test-First, Behavior-Focused Testing | PASS — the acceptance bar is explicitly behavioral (existing CLI-level tests' assertions must not change, per User Story 3/FR-004), plus new direct unit tests added for the consolidated module's own logic (de-dup, partial resolution, idempotency) rather than testing internal call counts. |
| V. Simplicity and No Speculative Features | PASS — explicitly declines the one speculative expansion identified in research (changing `agent_report.json` to a list-of-objects wire format) since no observed failure requires it yet; the refactor itself is justified by the constitution's own "written three times" threshold, not spec-driven from thin air. |
| VI. Structured, Guardrailed Agent Invocation | PASS — no change to how agents are invoked, sandboxed, timed out, or validated; `AgentReport` parsing and the `_validate_*` re-verification steps are untouched. The module only changes what happens to already-validated, already-typed results after they're produced. |

No violations. **Complexity Tracking section is not needed.**

## Project Structure

### Documentation (this feature)

```text
specs/008-consolidate-dependency-resolution/
├── plan.md              # This file
├── research.md          # Phase 0 output — current-state trace + 5 design decisions
├── data-model.md         # Phase 1 output — LibraryDependency/DependencySource/DependencyState
├── quickstart.md         # Phase 1 output — 4 validation scenarios + regression check
├── contracts/
│   └── dependency_resolution_api.md   # Phase 1 output — internal module API contract
└── tasks.md              # Phase 2 output (/speckit-tasks command — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/harnessbuddy/library_builder/
├── dependency_resolution.py    # NEW — DependencySource, LibraryDependency, DependencyState,
│                                #        from_static_probe, from_agent_report, merge,
│                                #        load_state, save_state
├── models.py                    # unchanged
├── agents.py                    # unchanged
├── harness_explorer.py          # unchanged
└── package_names.py             # unchanged — called from dependency_resolution.py instead of
                                  #             directly from cli.py

src/harnessbuddy/cli.py          # _ProjectState/_empty_state/load_project_state/
                                  # save_project_state/merge_packages_into_state removed;
                                  # _run_library_phase, _run_harness_phase, and the two
                                  # BuildFailureError/LLMBudgetError handlers in _cmd_generate
                                  # call dependency_resolution's functions instead

tests/library_builder/
└── test_dependency_resolution.py   # NEW — direct unit tests (quickstart Scenario 4)

tests/test_cli.py                  # output-asserting tests keep unchanged assertions
                                    # (quickstart Scenario 1 is the gate); the dedicated block of
                                    # ~8 tests under the "load_project_state / save_project_state
                                    # / merge_packages_into_state" comment (~line 1235 today,
                                    # e.g. test_save_and_load_project_state_roundtrip) tests
                                    # those functions directly by name — they migrate to
                                    # test_dependency_resolution.py with the same behavioral
                                    # assertions, calling the new module's load_state/save_state/
                                    # merge instead. This is a relocation, not a rewrite: the
                                    # code under test moved, so the test file testing it by name
                                    # moves too.
```

**Structure Decision**: Single existing project, no restructuring at the package level. The new
module lands inside the already-established `harnessbuddy.library_builder` package (Constitution
Principle II/III), alongside its sibling modules `harness_explorer.py`/`package_names.py`/
`agents.py` that it now sits between (consuming the first three, replacing scattered logic
`cli.py` used to inline).

## Complexity Tracking

*No Constitution Check violations — this section is intentionally empty.*
