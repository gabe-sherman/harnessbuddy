# Implementation Plan: Build Statistics Reporting

**Branch**: `003-build-stats-reporting` | **Date**: 2026-07-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-build-stats-reporting/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Each `harnessbuddy generate` run currently reports agent time/cost/tokens only inside
per-invocation transcript log files (`agent_library_build.log`,
`agent_harness_build.log`), and reports nothing at all about total run time or a final
pass/fail classification. This feature adds a `RunStats` record — total run duration,
per-phase (library-build, harness-build) agent invocation status with duration, cost,
and a plain-language summary of what the agent did (or `"N/A"` if that agent wasn't
invoked), and a final status (`success` / `failed_library_build` /
`failed_harness_build`) — written once as `stats.json` at the parent output directory
(alongside, not inside, `local/` and `oss-fuzz/`). Because a harness failure that still
emits stub output must be reported as `failed_harness_build` (not `success`), and stats
must be written even when a build phase fails outright, this requires: (1) capturing the
agent's own final message during streaming (new `AgentStreamResult.final_message`
field), (2) attaching agent duration/cost/summary to `BuildFailureError`/`LLMBudgetError`
so it survives the exception path, and (3) creating the shared output directory eagerly
in `cli.py` rather than as a side effect of successful generation.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: None new. Uses the standard library (`json`, `time`,
`dataclasses`, `enum`, already-used `pathlib`).

**Storage**: Local filesystem only — one JSON file (`stats.json`) per run, written to
the run's output directory. No database, no network storage.

**Testing**: `pytest`, following the existing patterns in `tests/library_builder/` (pure
dataclass/serialization tests, no mocking needed for `stats.py` itself since it has no
I/O boundary beyond a single `Path.write_text`) and `tests/test_cli.py` (patching
`build_library`/`build_harness` with fake results to assert the written `stats.json`
content and path).

**Target Platform**: Same as the rest of HarnessBuddy — Linux/macOS CLI, no new
platform constraints.

**Project Type**: Single project (CLI tool) — no new top-level project/package.

**Performance Goals**: Negligible — one JSON serialization and one file write per run,
after all build work is already done. No performance requirement beyond "does not
noticeably delay run completion."

**Constraints**: No new mandatory external dependency (Principle V). Must not change the
existing per-agent transcript/log file format or location (spec Assumptions). Must not
write `stats.json` when no output directory is ever established (repo-ingestion or
analysis failure) — per FR-012 and the spec's edge cases, absence of an output directory
means absence of a stats file, not an empty/partial one written elsewhere.

**Scale/Scope**: One new module (`library_builder/stats.py`), three call sites updated
in `cli.py` (`_generate_outputs` success path, and the two `except BuildFailureError`
blocks in `_cmd_generate`), plus targeted additions to `agent_stream.py`/`agents.py` to
carry the new `final_message` field and to `BuildFailureError`/`LLMBudgetError` to carry
an `AgentRunSummary`. No change to `local/generation.py` or `oss_fuzz/generation.py`
beyond `_cmd_generate` now creating their shared parent directory before they run.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I. Code Quality Is Non-Negotiable | `stats.py` is small, single-purpose functions (one converter per phase, one status-classifier, one writer) well under the 100-line/complexity-8 limits. Absolute imports, type annotations, and docstrings apply as everywhere else. **Pass.** |
| II. Modular Package Boundaries | `stats.py` lives in `library_builder/` (not `core/`) because it is specific to this tool's two build phases and output layout — matches `models.py`/`agents.py` placement. `RunStats`/`AgentPhaseStats`/`RunStatus` are typed dataclasses/enums, never loose dicts, satisfying the "cross-module contracts MUST be typed" rule. **Pass.** |
| III. Extensible Multi-Tool Architecture | This feature is scoped entirely inside `library_builder`; it doesn't touch `core/` in a way that assumes future tools need identical stats shapes. If a second tool later needs equivalent reporting, the `RunStats` shape (not code) can be referenced without `core/` needing to know about it yet — no speculative generalization added now. **Pass.** |
| IV. Test-First, Behavior-Focused Testing | `stats.py`'s conversion/classification/serialization functions are pure and tested directly with real `BuildExplorationResult`/`HarnessExplorationResult`/exception instances — no mocking (not a genuine boundary). The one I/O call (`Path.write_text`) is exercised through `tmp_path` in tests, not mocked. `cli.py` integration tests continue the existing pattern of patching `build_library`/`build_harness` (the genuine subprocess/agent boundary), not `stats.py` itself. Edge cases (agent invoked but no cost, no final message, phase raised instead of returning) each get an explicit test per the spec's edge cases. **Pass.** |
| V. Simplicity and No Speculative Features | Exactly the three status values and three per-phase fields the spec asks for — no extra status enum members for `OutputDirectoryExistsError` or other failure modes the spec doesn't mention (data-model.md Non-goals). No new CLI flag: `stats.json` is written unconditionally, matching "just write it" rather than adding an opt-out no one asked for. **Pass.** |
| VI. Structured, Guardrailed Agent Invocation | `final_message` is captured using the same typed content-block discrimination `agent_stream.py` already performs (Claude `text` blocks / Codex `agent_message` items) — no new raw-text scraping. `BuildFailureError`/`LLMBudgetError` gain a typed `summary: AgentRunSummary` field instead of stats being reconstructed by re-parsing the exception's `.output` text, keeping the exception's contract typed rather than string-shaped. **Pass.** |

No violations — Complexity Tracking section not needed.

## Project Structure

### Documentation (this feature)

```text
specs/003-build-stats-reporting/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── stats-json.md    # Phase 1 output — stats.json field contract
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/harnessbuddy/
├── core/
│   └── agent_stream.py        # updated: AgentStreamResult gains final_message: str | None,
│                                #          populated from the last genuine assistant text
│                                #          block (Claude `text` / Codex `agent_message`);
│                                #          AgentRunSummary gains the same field
├── library_builder/
│   ├── agents.py               # updated: BuildFailureError/LLMBudgetError gain a
│   │                            #          `summary: AgentRunSummary` attribute set before
│   │                            #          raising; BuildExplorationResult/
│   │                            #          HarnessExplorationResult gain `agent_summary: str | None`
│   ├── models.py                # updated: add agent_summary field to both result dataclasses
│   ├── stats.py                 # NEW: RunStatus, AgentPhaseStats, RunStats dataclasses;
│   │                             #      conversion helpers from results/exceptions;
│   │                             #      write_run_stats(path, stats) -> None
│   └── (local/, oss_fuzz/)       # unchanged — stats.json is a sibling, not written by these
└── cli.py                       # updated: create the shared output directory right after
                                  #          _resolve_output_paths(); start a run timer at the
                                  #          top of _cmd_generate; build and write RunStats at
                                  #          the success path in _generate_outputs and in both
                                  #          except BuildFailureError/LLMBudgetError blocks

tests/
├── core/
│   └── test_agent_stream.py    # updated: assert final_message population/absence for both
│                                 #          backends
├── library_builder/
│   ├── test_agents.py           # updated: assert BuildFailureError/LLMBudgetError carry
│   │                             #          summary; assert agent_summary on results
│   └── test_stats.py            # NEW: pure unit tests for RunStats/AgentPhaseStats
│                                  #      construction, classification, and JSON shape
└── test_cli.py                  # updated: assert stats.json is written with correct content
                                  #          for success / agent-repaired success / library
                                  #          failure / harness failure-with-stub-output, and
                                  #          NOT written on pre-output-directory failures
```

**Structure Decision**: Single project, no new top-level package. This is an internal
capability of the existing `library_builder` tool, implemented as one new module
(`library_builder/stats.py`) plus targeted additions to the existing agent-stream/agent
and CLI orchestration modules that already own the data this feature reports on.

## Complexity Tracking

*No entries — Constitution Check reported no violations.*
