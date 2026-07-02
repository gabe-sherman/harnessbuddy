# Implementation Plan: Harness Linker Dependencies Become Install Commands

**Branch**: `005-harness-system-packages` | **Date**: 2026-07-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-harness-system-packages/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

`explore_harness_compilation` (and the agent-repair path in `agents.py`) only
translates linked libraries into apt/brew packages when the linker *fails* to
find them (`missing_system_libs`, extracted from stderr). When the harness
links successfully because the exploration machine already has the library
(e.g. zstd/lz/lzma, confirmed against the libtiff ground-truth run, where
`compile_harnesses.sh` embeds `-lzstd -lz -llzma` but neither the generated
Dockerfile nor `setup.sh` install anything), those packages never reach the
generated output — so the harness link step fails the first time it runs
somewhere clean (a fresh OSS-Fuzz Docker build, a teammate's machine, CI).

The fix widens the single existing merge point (`_run_harness_phase` in
`cli.py`) to also translate `HarnessExplorationResult.transitive_link_flags`
(the `-lxxx` flags baked into `compile_harnesses.sh`/`build.sh` regardless of
outcome) through the existing `package_names.translate()` machinery, unioned
with the existing `missing_system_libs` signal, before merging into project
state. No new state, no new generated-file logic, no new CLI flags — the
existing `_ProjectState`/`merge_packages_into_state`/Dockerfile/setup.sh
plumbing already does the right thing once it's given the right input.

## Technical Context

**Language/Version**: Python 3.13, managed with `uv`

**Primary Dependencies**: None new. Reuses existing in-repo modules:
`harnessbuddy.library_builder.package_names` (translation), `harnessbuddy.cli`
(`_run_harness_phase`, `merge_packages_into_state`), `harnessbuddy.library_builder.harness_explorer`
(source of `transitive_link_flags`).

**Storage**: Existing per-project `state.json` (`_ProjectState`: `apt_packages`,
`brew_packages`, `unknown_libs`, `sources`) written via `save_project_state` —
no schema change, only a wider set of inputs merged into the existing fields.

**Testing**: `pytest -q` (`uv run pytest -q`), extending
`tests/test_cli.py`, `tests/library_builder/test_harness_explorer.py`, and
`tests/library_builder/test_package_names.py`.

**Target Platform**: HarnessBuddy itself runs on the developer's Linux or
macOS host; the generated artifacts target the OSS-Fuzz base builder image
(Debian/Ubuntu, apt) for the Dockerfile and the developer's own host (apt on
Linux, brew on macOS) for `setup.sh`.

**Project Type**: Single project (Python CLI tool) — matches Option 1 below.

**Performance Goals**: N/A — this is list deduplication and dictionary lookup
over a handful of strings per run; not performance-sensitive.

**Constraints**: Must not change `HarnessExplorationResult`'s existing
`missing_system_libs` semantics (linker-reported-missing, used verbatim in the
failure warning message and in `agents.py`'s prompt to the harness-repair
agent) — the fix must be additive at the merge point, not a redefinition of
that field. Must not duplicate packages already contributed by the library
build phase. Must not regress the existing missing-locally failure path.

**Scale/Scope**: Confined to `cli.py`'s `_run_harness_phase` plus one small
pure helper (flag → bare library name) placed next to
`transitive_link_flags`'s producer in `harness_explorer.py`. No changes to
`local/generation.py` or `oss_fuzz/generation.py` — they already consume
`analysis.system_packages` / `brew_packages` correctly once those are populated.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Code Quality**: New/changed functions stay well under the 100-line /
  complexity-8 limits (this is a small, additive change to an existing
  ~40-line function plus one ~5-line pure helper). No new relative imports.
  Docstring updates where behavior changes. **PASS**.
- **II. Modular Package Boundaries**: The new pure helper lives in
  `library_builder/harness_explorer.py` (the module that already owns
  `transitive_link_flags` and `_extract_missing_system_libs`), not in `cli.py`
  or `core/`. `cli.py` stays limited to orchestration (calling the helper and
  the existing `translate`/`merge_packages_into_state`), per its constrained
  role. **PASS**.
- **III. Extensible Multi-Tool Architecture**: No new tool, no cross-tool
  coupling introduced. **PASS / N/A**.
- **IV. Test-First, Behavior-Focused Testing**: New tests will exercise
  observable behavior (generated `state.json` / Dockerfile / setup.sh
  contents given a harness result with populated `transitive_link_flags` and
  empty `missing_system_libs`), not internal call sequencing. No Docker or
  network required — `HarnessExplorationResult` is constructed directly in
  tests as it already is in `test_harness_explorer.py`. **PASS**.
- **V. Simplicity and No Speculative Features**: No new CLI flags, no new
  config, no new persisted fields — reuses `_ProjectState` and
  `merge_packages_into_state` exactly as they exist today. **PASS**.
- **VI. Structured, Guardrailed Agent Invocation**: Not touched — the fix
  applies uniformly to whatever `HarnessExplorationResult` `build_harness`
  returns (deterministic or agent-repaired), without changing agent prompts,
  guardrails, or result normalization. **PASS / N/A**.

No violations to justify; Complexity Tracking table is empty.

## Project Structure

### Documentation (this feature)

```text
specs/005-harness-system-packages/
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
├── cli.py                                   # _run_harness_phase: widen merge-point input
└── library_builder/
    ├── harness_explorer.py                  # new pure helper: flag -> bare lib name
    ├── package_names.py                     # unchanged; reused as-is
    └── package_names.json                   # unchanged; existing mapping table

tests/
├── test_cli.py                              # _run_harness_phase merge behavior
└── library_builder/
    ├── test_harness_explorer.py             # new helper's unit tests
    └── test_package_names.py                # unchanged; existing coverage still applies
```

**Structure Decision**: Single project (Option 1). This feature is a
behavioral fix confined to the existing `harnessbuddy.cli` orchestration layer
and the existing `harnessbuddy.library_builder` tool package — no new
top-level package, no new project type. It follows Constitution Principle II
by keeping the new pure logic in `library_builder/harness_explorer.py` (next
to the `transitive_link_flags` it operates on) rather than in `cli.py`.

## Post-Design Constitution Check

*Re-evaluated after Phase 1 (research.md, data-model.md, contracts/,
quickstart.md).*

Design confirmed the Phase 0 assessment did not change: no new state, no new
generated-file logic, no new module, no agent-invocation changes. All six
principles remain **PASS** for the reasons stated in the Constitution Check
above; data-model.md and contracts/generated-install-step.md confirm the
change is additive to existing types (`HarnessExplorationResult`,
`_ProjectState`) rather than a schema or contract change. No new violations
to record in Complexity Tracking.

## Complexity Tracking

> No Constitution Check violations — this section is intentionally empty.
