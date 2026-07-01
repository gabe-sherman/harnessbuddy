# Research: Build Statistics Reporting

## 1. Where agent duration/cost/summary data already lives

**Decision**: Populate the new stats record directly from the existing typed result
dataclasses (`BuildExplorationResult`, `HarnessExplorationResult`) and their `llm_used`
flag, rather than re-deriving anything from raw transcripts at stats-write time.

**Rationale**: `duration_seconds`/`cost_usd`/`input_tokens`/`output_tokens` on both
dataclasses are dual-purpose today: when `llm_used=False`, `duration_seconds` holds the
*deterministic build's* duration and `cost_usd` is always `None`; when `llm_used=True`,
both fields are overwritten with the *agent invocation's* own duration/cost
(`agents.py:182-198`, `agents.py:277-294`). The new stats module must gate on
`llm_used`, not on whether `cost_usd`/`duration_seconds` are populated, or a
deterministic build's duration would be misreported as "agent time."

**Alternatives considered**: Re-parsing the persisted `agent_*.log` report files at
stats-write time. Rejected — the data is already in memory as typed fields at the point
`_cmd_generate` has both results; re-parsing a text file the same process just wrote
would duplicate logic and violate Principle VI's "normalize into typed results, don't
leak raw text into the pipeline."

## 2. Capturing "the LLM's summary of the work it did" (new capability)

**Decision**: Add a `final_message: str | None` field to `AgentStreamResult`, populated
in `run_agent_streaming()` by tracking the *last* genuine assistant text block seen
while parsing the stream — Claude's `text` content-block type and Codex's
`agent_message` item type specifically (not `thinking`/`reasoning`, not tool-use
announcements, not tool results). Thread it through `AgentRunSummary` and into
`BuildExplorationResult.agent_summary` / `HarnessExplorationResult.agent_summary`
alongside the existing `cost_usd`/`duration_seconds` fields.

**Rationale**: `agent_stream.py` today tags Claude `text` blocks, `thinking` blocks, and
tool-use announcements all as the same `"status"` `AgentActivityKind`
(`agent_stream.py:63-74`, `_codex_item_event` at `agent_stream.py:109-125`) and joins
them into one undifferentiated `combined_text` blob (`agent_stream.py:204-243`). There
is currently no way to recover "just the agent's own final words" from that blob without
re-parsing raw JSON lines, which Decision 1 already rules out. Tracking the *last* real
text block as a separate variable while the line-by-line loop already runs costs one
assignment per matching event and reuses the existing block-type discrimination in
`_claude_content_block_event`/`_codex_item_event` — no new JSON parsing paths.

**Alternatives considered**:
- *Add a dedicated LLM call that asks the agent to produce a summary.* Rejected — the
  spec explicitly defines this as the agent's own final message, not a
  separately-prompted summarization step (spec Assumptions); it would also double
  agent invocation cost for a field that already exists in the transcript.
- *Store the full `combined_text` as the "summary."* Rejected — this is what the
  `agent_*.log` transcript file already is; duplicating it in the stats JSON as
  "summary" would contradict the spec's intent (a short at-a-glance description) and
  bloat every stats file with a full transcript.

## 3. Recovering agent stats when an agent invocation raises instead of returning

**Decision**: Attach a `summary: AgentRunSummary` field to `BuildFailureError` and
`LLMBudgetError` (currently they carry only `.output: str`), populated at the point in
`invoke_library_builder_agent`/`invoke_harness_builder_agent` where `_report_agent_run`
already builds an `AgentRunSummary` (`agents.py:171` / `agents.py:254`) — i.e. attach it
right before `_raise_for_agent_failure` is called, so the exception carries the same
duration/cost/summary data the log file already recorded.

**Rationale**: `_raise_for_agent_failure` (`agents.py:93-100`) raises before
`invoke_library_builder_agent`/`invoke_harness_builder_agent` construct and return a
`BuildExplorationResult`/`HarnessExplorationResult`. Today that means an agent run that
hits `ACTION REQUIRED` or a budget limit loses its duration/cost/summary entirely once
the exception propagates to `cli.py` — there is no typed object left holding it. Since
FR-003/FR-005 require recording agent time/cost/summary even when the agent invocation
does not lead to a successful build, and `_cmd_generate`'s `except BuildFailureError`
blocks (`cli.py:429-434`, `cli.py:445-450`) are exactly where this data is needed to
populate the stats record before returning `1`, the exception itself is the natural
carrier — no new global state or side channel required.

**Alternatives considered**: Writing the stats file from inside `agents.py` at the
moment of raising, instead of from `cli.py`. Rejected — `cli.py` is the only place that
knows the run's total duration, both phases' outcomes, and the final output directory
path; splitting stats-writing across two modules would mean no single place owns "when
is the stats file considered final."

## 4. Ensuring an output directory exists before build phases run (for FR-012)

**Decision**: Have `_cmd_generate` create the shared parent output directory (the one
computed by `_resolve_output_paths`, containing `local/` and `oss-fuzz/`) immediately
after `_resolve_output_paths` returns — before the library-build phase starts — rather
than relying on `generate_local`/`generate_oss_fuzz` to lazily create it via
`output_path.mkdir(parents=True)`.

**Rationale**: Today, `base_output` (the parent of `local/`/`oss-fuzz/`) only comes into
existence as a side effect of `generate_local`/`generate_oss_fuzz` succeeding
(`local/generation.py:32`, `oss_fuzz/generation.py:67`), which only runs after both
build phases succeed or fail non-fatally. FR-012 requires writing statistics "as long as
an output directory exists" even when a run fails during the library or harness build —
but under current behavior, a library-build failure means no output directory is ever
created, so there would be nowhere to put the stats file. Creating the parent directory
eagerly (once, right after the overwrite-confirmation step) gives every run past that
point a place to write `stats.json`, without changing where `local/`/`oss-fuzz/`
themselves get created.

**Alternatives considered**: Writing the stats file to the per-project workspace
(`project_dir(state_dir, ...)`) instead of the output directory when the output
directory doesn't exist yet. Rejected — FR-008 and the user's own request are explicit
that stats belong in "the output directory of the build," and the workspace directory
is an internal working area, not a user-facing output location.

## 5. Placement of the new stats module

**Decision**: New module `harnessbuddy/library_builder/stats.py`, defining the
`RunStats`/`AgentPhaseStats`/`RunStatus` types and a `write_run_stats()` function; called
from `cli.py` at the two points a run can conclude (normal completion in
`_generate_outputs`, and the two `except BuildFailureError` blocks in `_cmd_generate`).

**Rationale**: Per Constitution Principle II, `harnessbuddy/core` holds only
tool-agnostic primitives; this feature is specific to the `library_builder` tool's two
build phases (library build, harness build) and its output-directory layout, so it
belongs in `library_builder`, matching where `models.py`/`agents.py` already live.

**Alternatives considered**: Extending `AgentRunSummary`/`format_agent_summary` in
`core/agent_stream.py` to also emit the pipeline-wide JSON file. Rejected — that module
is scoped to a single agent invocation's transcript/summary and is reused generically;
pipeline-wide status (which spans two independent, possibly-agent-less phases) does not
belong at that layer.
