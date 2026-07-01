# Phase 0 Research: Agent Run Introspection

## 1. Claude Code CLI structured output

**Decision**: Invoke `claude` with `--output-format stream-json --verbose` (in addition to
the existing `--print --permission-mode auto`) and parse the resulting newline-delimited
JSON stream incrementally as it arrives on stdout.

**Rationale**: `stream-json` is Claude Code's documented machine-readable event stream.
`--verbose` is required for the CLI to actually emit full stream-json output (a bare
`--output-format stream-json` without `--verbose` does not produce the same event detail).
The event stream includes `system` (session init), `assistant`/`user` (complete message
turns, each with content blocks — text, `tool_use`, `tool_result`), and a final `result`
event carrying exactly the aggregate stats this feature needs: `total_cost_usd`,
`duration_ms`, `duration_api_ms`, `num_turns`, `is_error`/`subtype`, and a `usage`/
`modelUsage` breakdown. This lets us both render live narration from message/tool events
and populate the final duration/cost summary from one authoritative event, without a
second API call or separate accounting pass.

**Alternatives considered**:
- Keep raw text passthrough (`--print` only, no `--verbose`/`stream-json`) — rejected,
  this is exactly today's unreadable-for-agent-details behavior the feature exists to fix,
  and it has no `result` event to source cost/duration from.
- `--include-partial-messages` for token-level incremental text streaming — rejected for
  v1: it adds a stream of `stream_event`/`content_block_delta` fragments that must be
  buffered and reassembled per content block before they're meaningful to render, for a
  readability gain (typing effect) the spec does not require. Complete `assistant`
  message events already arrive incrementally turn-by-turn, which is sufficient to satisfy
  "live, human-readable account of the agent's actions as they happen" (spec US1).

## 2. Codex CLI structured output

**Decision**: Invoke `codex exec` with `--json` and parse its newline-delimited event
stream (`thread.started`, `turn.started`, `item.*` for commands/file changes, and
`turn.completed`/`turn.failed`) the same way as the Claude stream. Populate duration from
wall-clock measurement (as today). Report token usage (`input_tokens`, `output_tokens`
from `turn.completed`'s `usage` object) as the Codex backend's cost-fallback metric,
rather than a dollar cost.

**Rationale**: Codex's `turn.completed` event exposes a `usage` object with token counts
(`input_tokens`, `output_tokens`, `cached_input_tokens`) but **no dollar-cost field**
anywhere in the CLI's own output — confirmed against the official CLI reference.
Converting token counts to a dollar figure would require this project to embed and
maintain an external, backend-specific pricing table, which is a feature the spec did not
ask for and which the constitution's simplicity principle (no speculative features)
counsels against. Token counts, however, are not an estimate — they are a number the
backend already reports — so surfacing them directly gives the user a real usage signal
instead of nothing. FR-006 requires exactly this fallback; FR-010 covers the (currently
never-hit, since Codex always reports `usage`) case where a backend reports neither.

**Alternatives considered**:
- Hardcode a per-model USD/token pricing table to synthesize a Codex cost figure —
  rejected: pricing changes over time and per model/tier, making this a maintenance
  burden and a source of quietly-wrong numbers; not requested by the spec.
- Show only "cost unavailable" with no other metric — rejected per explicit feature
  request: the token counts are already computed and present in the CLI's own output at
  zero extra cost to surface, so discarding them in favor of a bare "unavailable" throws
  away a real, free signal about how much work the invocation did.

## 3. Rendering approach (no new dependency)

**Decision**: Hand-roll a small, pure JSONL-event-to-readable-line renderer in this
codebase rather than adopting a third-party formatter.

**Rationale**: Existing third-party stream-json pretty-printers (e.g. Khan Academy's
`format-claude-stream`) are Node/TypeScript tools meant to be piped after the `claude`
process — not a Python library this project could import — and none exist at all for
Codex's `--json` event shape. Given both backends must be normalized to the same
narration style regardless, and the mapping from event type to a readable line is a small
number of cases (assistant text, tool use, tool result, turn/session boundaries), a
focused in-repo renderer is simpler than wiring an external process per backend and avoids
adding an unjustified dependency (per project dependency-justification standard).

**Alternatives considered**:
- Shell out to `format-claude-stream` as a second subprocess for the Claude path only,
  and hand-roll only the Codex path — rejected: two different rendering code paths with
  different fidelity/behavior for the two backends contradicts spec edge case "both must
  produce equivalent readable narration," and only covers one backend anyway.

## 4. Integration point and package placement

**Decision**: Implement JSONL parsing, human-readable rendering, and stats extraction as
a new tool-agnostic module in `harnessbuddy/core/` (alongside the existing
`run_command_streaming`), consumed by `library_builder/agents.py` for both the
library-build and harness-build agent invocations.

**Rationale**: Constitution Principle II reserves `core/` for generic, tool-agnostic
primitives including subprocess execution, and explicitly bars tool-specific assumptions
there. Parsing a backend's event stream and turning it into readable lines is agnostic to
*why* the agent was invoked (library build repair vs. harness link repair) — it depends
only on which backend (`claude`/`codex`) produced the stream. Both current call sites in
`library_builder/agents.py` already duplicate near-identical invocation/guardrail logic;
adding a third near-duplicate for streaming would compound that, whereas a single core
implementation serves both today and any future tool that adds agent fallback (Principle
III), consistent with `run_command_streaming` already living in `core/subprocesses.py` for
the same reason.

**Alternatives considered**:
- Keep the new logic inside `library_builder/agents.py` — rejected: works for the two
  current call sites but violates Principle III's "adding a tool never requires modifying
  an existing tool's internals" the first time a second tool needs agent fallback, and
  duplicates logic that has no dependency on library/harness build semantics.

## 5. Subprocess integration model

**Decision**: Read stdout as line-buffered JSONL from a single `Popen` per invocation
(same process-lifecycle model as today's `run_command_streaming`), decoding one JSON
object per line as it arrives.

**Rationale**: Both `claude --print` and `codex exec` are confirmed one-shot,
non-interactive commands that run to completion and exit — not long-lived servers — so
the existing "spawn, stream stdout, wait for exit" model already used by
`run_command_streaming` is the correct integration point; no persistent connection, IPC,
or polling is needed.

**Alternatives considered**: None — this matches the existing, working integration model
for both backends and no evidence suggests it's insufficient for JSONL vs. plain text.
