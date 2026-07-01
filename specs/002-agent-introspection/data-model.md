# Phase 1 Data Model: Agent Run Introspection

This feature adds fields to the existing typed result dataclasses in
`harnessbuddy/library_builder/models.py` rather than introducing new persisted domain
entities — per Constitution Principle VI, agent results must stay normalized into the
same typed structures used by the deterministic path.

## AgentInvocationStats (new, embedded in existing result dataclasses)

Represents the outcome-independent facts about one agent invocation. Not a standalone
persisted record — it is a set of fields carried on `BuildExplorationResult` and
`HarnessExplorationResult` (both already returned by `invoke_library_builder_agent` /
`invoke_harness_builder_agent`).

| Field | Type | Notes |
|---|---|---|
| `duration_seconds` | `float` | Wall-clock time for the invocation. Already exists on `BuildExplorationResult`; **new** on `HarnessExplorationResult` (currently dropped — see research/exploration finding). |
| `cost_usd` | `float \| None` | Monetary cost reported by the backend for this invocation. `None` means unavailable (Codex today), never a synthesized estimate. **New** on both result dataclasses. |
| `input_tokens` | `int \| None` | Input token count reported by the backend, used as the cost-fallback metric (FR-006) when `cost_usd` is `None`. Only the Codex path populates this today — Claude's cost figure already answers the question, so its token counts are not separately extracted. **New** on both result dataclasses. |
| `output_tokens` | `int \| None` | Output token count, same population rule as `input_tokens`. **New** on both result dataclasses. |
| `transcript_path` | `Path \| None` | Location of the persisted human-readable transcript + summary file for this invocation (FR-009). `None` only when no agent was invoked. **New** on both result dataclasses. |

Validation rules:
- `duration_seconds` MUST be measured regardless of outcome (success, failure, budget
  limit, timeout) — FR-004.
- `cost_usd` MUST be `None` rather than `0.0` when the backend does not report cost —
  FR-006 (zero is a misleading value, not "unavailable").
- `input_tokens`/`output_tokens` MUST be populated whenever `cost_usd` is `None` and the
  backend's final event reports token usage (FR-006) — never populated with a
  synthesized or estimated value.
- When `cost_usd` is `None` and `input_tokens`/`output_tokens` are also both `None` (no
  backend data at all), the persisted/printed summary MUST say so explicitly rather than
  going blank — FR-010.
- `transcript_path` MUST point to a file that exists once the invocation function returns
  successfully or raises `BuildFailureError`/`LLMBudgetError` (both carry `.output`, the
  full raw text, already — this file is written before either exception is raised so
  diagnosis is never blocked on it).

## AgentActivityEvent (new, internal/transient — not part of the public result contract)

One parsed unit of a backend's structured event stream, used only to drive live rendering
and the persisted transcript. Not stored on the result dataclasses (raw stdout/stderr
already must not leak into the pipeline contract per Principle VI) — it exists only while
an invocation is in progress and while writing the transcript file.

| Field | Type | Notes |
|---|---|---|
| `kind` | enum: `status`, `file_read`, `file_edit`, `command_run`, `tool_result`, `raw_fallback` | `raw_fallback` covers a line that failed to parse as a recognized event — rendered verbatim rather than dropped (spec edge case: malformed output must still be visible). |
| `text` | `str` | The human-readable line to print/write for this event. |

Validation rules:
- A line that cannot be parsed into a known event type MUST still produce exactly one
  `AgentActivityEvent` (`kind=raw_fallback`) rather than being silently discarded.
- Event ordering as parsed MUST match the order lines arrived on stdout — no reordering
  or buffering beyond what is needed to assemble one complete event from one line.

## Outcome (existing enums/fields, unchanged shape, now consistently populated)

`BuildExplorationResult.succeeded` / `HarnessExplorationResult.succeeded` (existing
`bool` fields) continue to represent success/failure. Budget-limit and timeout outcomes
continue to surface via the existing `LLMBudgetError` exception and `exit_code == -1`
convention respectively — this feature does not add a new outcome representation, it
ensures `duration_seconds`/`cost_usd`/`transcript_path` are populated on the result object
that exists at the moment each of those outcomes is determined (FR-004 requires reporting
even on failure/budget-limit/timeout).
