# Implementation Plan: Complete Library Dependency Packaging

**Branch**: `main` (no feature branch created — no `before_specify`/`before_plan` hook
registered in this project) | **Date**: 2026-07-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-complete-dependency-packaging/spec.md`

## Summary

Every library dependency HarnessBuddy's harness link step relies on must reach the generated
`Dockerfile`/`setup.sh` install commands, and every dependency's `-l` flag must be resolved
(deterministically or by an agent) before falling back to naming a package. Most of this is
already implemented earlier in this session (see `research.md`): `AgentReport` now carries
`missing_libs`/`missing_apt_packages`/`missing_brew_packages` instead of one ambiguous field,
the agent supplies real per-platform package names itself rather than through a static lookup
table, and a bug that silently discarded the deterministic probe's findings on a validation-only
agent failure is fixed. The one remaining gap: `agents/harness_builder/SKILL.md` only asks the
agent to report package names on its *unresolvable-failure* path, not when it resolves a link
successfully using a library it recognizes but HarnessBuddy's static table doesn't — so an
agent-resolved-but-unmapped dependency currently reaches the harness script's `-l` flags but
never the install commands. Closing that gap is a `SKILL.md` prompt change plus regression
coverage; no data model or architecture change is required.

## Technical Context

**Language/Version**: Python 3.13 (`uv venv`)

**Primary Dependencies**: none new — stdlib `json`/`re`/`subprocess` plus the existing
`harnessbuddy.core.agent_stream` (subprocess streaming) and `harnessbuddy.library_builder`
internals (`models`, `agents`, `harness_explorer`, `package_names`, `cli`)

**Storage**: `.harnessbuddy/<project>/state.json` (flat JSON, no database) via
`cli.load_project_state`/`save_project_state`

**Testing**: `pytest` (`uv run pytest -q`), no Docker/network by default per Constitution
Principle IV

**Target Platform**: Author's dev machine (Linux or macOS) for the deterministic/agent
exploration phases; generated output targets a Debian/Ubuntu OSS-Fuzz Docker image (Dockerfile)
and the local dev machine's own platform (`setup.sh`)

**Project Type**: Single Python CLI project (`src/harnessbuddy/`), no frontend/backend split

**Performance Goals**: N/A — this feature changes what data an already-fast (sub-second,
subprocess-mocked in tests) pipeline records and reports, not its performance characteristics

**Constraints**: Zero new runtime dependencies; must not require every possible C/C++ library to
be pre-catalogued in `package_names.json` (the specific design flaw this feature moves away
from, per `research.md`)

**Scale/Scope**: One `SKILL.md` prompt-instruction change, one new regression test in
`tests/test_cli.py`; no changes to `models.py`, `agents.py`, `harness_explorer.py`,
`package_names.py`, or the generation modules (`local/generation.py`, `oss_fuzz/generation.py`)
are needed — `research.md` confirms the plumbing they already carry is unconditional and
sufficient

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I. Code Quality Is Non-Negotiable | PASS — no new functions beyond a small test addition; existing lint/type/test gates apply unchanged. |
| II. Modular Package Boundaries | PASS — all touched code stays inside `library_builder/` (`agents/harness_builder/SKILL.md`, `tests/test_cli.py`); no `core/` changes, no cross-tool reach-through. |
| III. Extensible Multi-Tool Architecture | PASS (not applicable) — no new tool, no change to how tools register with `cli.py`. |
| IV. Test-First, Behavior-Focused Testing | PASS — the new test (quickstart Scenario 1) exercises the actual generated `Dockerfile`/`setup.sh` output of a mocked agent subprocess, not internal implementation details; matches the project's existing convention of trimming shallow "write JSON, assert field copied" tests in favor of end-to-end behavior assertions (done earlier this session per user direction). |
| V. Simplicity and No Speculative Features | PASS — explicitly rejects two speculative options in `research.md` (renaming fields for clarity with no behavioral need; pre-cataloguing every possible library in a static table) in favor of the minimal instruction change that closes the actual gap. |
| VI. Structured, Guardrailed Agent Invocation | PASS — the agent's self-reported package names were already being treated as trusted diagnostic metadata (not a success/failure verdict) prior to this feature, consistent with how `extra_include_paths`/`extra_library_paths` are already handled; `HarnessExplorationResult.succeeded` continues to be independently re-validated via `_validate_harness_artifacts` regardless of what the agent reports. No change to guardrails (sandbox, timeout, `ACTION_REQUIRED` escape hatch) is needed. |

No violations. **Complexity Tracking section is not needed.**

## Project Structure

### Documentation (this feature)

```text
specs/007-complete-dependency-packaging/
├── plan.md              # This file
├── research.md          # Phase 0 output — gap analysis against already-implemented work
├── data-model.md        # Phase 1 output — maps spec entities onto existing dataclasses
├── quickstart.md        # Phase 1 output — validation scenarios
├── contracts/
│   └── agent_report_schema.md   # Phase 1 output — agent_report.json contract, behavioral delta
└── tasks.md              # Phase 2 output (/speckit-tasks command — NOT created by /speckit-plan)
```

### Source Code (repository root)

No new files or directories. This feature touches:

```text
agents/harness_builder/SKILL.md              # step 4: add package-reporting instruction
tests/test_cli.py                            # new regression test (quickstart Scenario 1)
```

Already modified earlier this session (context for `/speckit-tasks`, not new work under this
plan) and left as-is per `research.md`:

```text
src/harnessbuddy/library_builder/models.py       # AgentReport / *ExplorationResult fields
src/harnessbuddy/library_builder/agents.py       # missing_libs -> -l flag synthesis, bugfix
src/harnessbuddy/library_builder/exploration.py  # read_agent_report field parsing
agents/library_builder/SKILL.md                  # apt/brew reporting for the library-build agent
tests/library_builder/test_agents.py             # updated + de-shallowed regression coverage
tests/library_builder/test_exploration.py        # updated field-parsing coverage
tests/test_cli.py                                # platform-aware apt/brew assertions (this session)
```

**Structure Decision**: Single existing project, no restructuring. All work lands inside the
already-established `harnessbuddy.library_builder` package and its paired `agents/` skill
instructions, following Constitution Principle II (modular package boundaries) and III
(extending a tool without touching another tool's internals — `feature_extractor` is untouched).

## Complexity Tracking

*No Constitution Check violations — this section is intentionally empty.*
