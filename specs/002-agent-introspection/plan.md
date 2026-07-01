# Implementation Plan: Agent Run Introspection

**Branch**: `002-agent-introspection` | **Date**: 2026-07-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-agent-introspection/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

When HarnessBuddy falls back to an LLM agent (`claude` or `codex`) to repair a failed
build or harness link, the user currently sees either nothing or raw subprocess text —
no view into what the agent is doing, and no record of how long it ran or what it cost.
This feature switches both backends to their structured JSONL event output
(`claude --output-format stream-json --verbose`, `codex exec --json`), renders that
stream into human-readable narration live on the terminal, and reports a duration/cost
summary after each invocation — persisting both the transcript and summary to a file in
the project workspace. Rendering and stats extraction are implemented once, in
`harnessbuddy/core/`, and reused by both existing agent call sites in
`library_builder/agents.py`.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: None new. Uses the standard library (`json`, `subprocess`,
already-used `dataclasses`/`pathlib`). Explicitly rejected a third-party stream-json
pretty-printer (Node-only, doesn't cover Codex) — see `research.md` §3.

**Storage**: Local filesystem only — one plain-text report file per agent invocation,
written to the existing project workspace directory (`.harnessbuddy/<project>/`). No
database, no network storage.

**Testing**: `pytest`, following the existing pattern in `tests/library_builder/test_agents.py`
of patching the core streaming call with a fake `RunResult`/event payload; new pure-logic
tests for JSONL parsing/rendering in `tests/core/` need no mocking (no I/O).

**Target Platform**: Same as the rest of HarnessBuddy — Linux/macOS CLI, no new platform
constraints.

**Project Type**: Single project (CLI tool) — no new top-level project/package.

**Performance Goals**: Rendering must not perceptibly lag behind the agent's own output
cadence — each JSONL line is parsed and rendered as it arrives, no batching/buffering
beyond what's needed to decode one complete JSON object per line.

**Constraints**: No new mandatory external dependency (Principle V / project dependency
policy). Must not lose any diagnostic detail current failure handling depends on
(`_BUDGET_PATTERN`/`ACTION_REQUIRED` matching against combined stdout+stderr text —
Principle VI) — the full raw text is still reconstructed and passed to
`_raise_for_agent_failure` even though the terminal/file view is now rendered, not raw.
Must degrade gracefully (render verbatim, don't crash or drop) on a line that isn't valid
JSON or doesn't match a known event shape.

**Scale/Scope**: Two existing call sites (`invoke_library_builder_agent`,
`invoke_harness_builder_agent`), two backends (`claude`, `codex`). No change to when or
how many times an agent is invoked (no new retry logic).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I. Code Quality Is Non-Negotiable | New JSONL parsing/rendering logic is broken into small, single-purpose functions (parse one line → one event; render one event → one line; accumulate final stats) so each stays well under the 100-line/complexity-8 limits. Absolute imports, type annotations, and docstrings apply as everywhere else. **Pass.** |
| II. Modular Package Boundaries | Parsing/rendering is backend-generic (depends only on which CLI produced the stream, not on library-build vs. harness-build semantics) and goes in `harnessbuddy/core/` alongside `run_command_streaming`, matching where equivalent generic subprocess logic already lives. `library_builder/agents.py` continues to own only prompt construction and result normalization. **Pass.** |
| III. Extensible Multi-Tool Architecture | Placing the renderer in `core/` means any future tool that adds agent fallback reuses it without modifying `library_builder` — satisfies "adding a tool never requires modifying an existing tool's internals." **Pass.** |
| IV. Test-First, Behavior-Focused Testing | Parsing/rendering are pure functions tested directly (no mocking — not a genuine boundary per Principle IV). The `Popen`/subprocess boundary itself continues to be the only mocked seam, matching existing `test_agents.py` patterns. Edge cases (malformed line, missing cost field, timeout) each get an explicit test. **Pass.** |
| V. Simplicity and No Speculative Features | No pricing table invented for Codex cost (spec's own FR-006 already covers "unavailable"); no new CLI flags — this replaces today's raw-passthrough behavior outright rather than adding an opt-in mode, per "replace, don't deprecate." **Pass.** |
| VI. Structured, Guardrailed Agent Invocation | `duration_seconds`, `cost_usd`, and `transcript_path` are added directly to the existing typed result dataclasses (`BuildExplorationResult`, `HarnessExplorationResult`) rather than introducing a parallel untyped stats object — keeps agent output normalized into the same typed contract the deterministic path uses. Raw stdout/stderr text is retained internally only long enough to feed the existing budget/action-required detection and the persisted report file; it does not leak into the pipeline as a substitute for the typed result. **Pass.** |

No violations — Complexity Tracking section not needed.

## Project Structure

### Documentation (this feature)

```text
specs/002-agent-introspection/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/
│   └── agent-run-report.md  # Phase 1 output — persisted transcript+summary file format
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/harnessbuddy/
├── core/
│   ├── subprocesses.py       # existing: run_command_streaming, RunResult
│   └── agent_stream.py       # NEW: JSONL event parsing (claude + codex shapes),
│                              #      human-readable rendering, final stats extraction
├── library_builder/
│   ├── agents.py              # updated: build stream-json/--json commands, call the new
│   │                           #          core runner, write the report file, populate
│   │                           #          duration_seconds/cost_usd/transcript_path
│   └── models.py               # updated: add cost_usd + transcript_path to
│                                #          BuildExplorationResult and HarnessExplorationResult;
│                                #          add duration_seconds to HarnessExplorationResult
└── cli.py                     # unchanged structurally; existing print statements around
                                # build_library()/build_harness() remain the human-facing
                                # entry point, now backed by richer result data

tests/
├── core/
│   └── test_agent_stream.py   # NEW: pure unit tests for parsing/rendering/stats,
│                                #      including malformed-line and missing-cost cases
└── library_builder/
    └── test_agents.py          # updated: assert new result fields and report file for
                                 #          both claude and codex payloads
```

**Structure Decision**: Single project, no new top-level package. This is an internal
capability of the existing `library_builder` tool's agent fallback path, implemented as
one new tool-agnostic module in `core/` (per Constitution Principle II) plus updates to
the two files that already own agent invocation and result typing.

## Complexity Tracking

*No entries — Constitution Check reported no violations.*
