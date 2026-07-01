# Data Model: Build Statistics Reporting

## `RunStatus` (enum, `library_builder/stats.py`)

| Value | Meaning |
|---|---|
| `SUCCESS` | Both build phases completed; harness build did not end in an unrecovered failure. |
| `FAILED_LIBRARY_BUILD` | The library build never reached a validated success (deterministic build failed and either no agent was available or the agent could not fix it, including the `BuildFailureError`/`LLMBudgetError` exception paths). |
| `FAILED_HARNESS_BUILD` | The harness compilation probe never reached a validated success (deterministic probe failed and either no agent was available or the agent could not fix it, including the exception paths) — reported even when the pipeline continues and emits stub harness output rather than halting. |

Serialized to JSON as its lowercase string `value` (e.g. `"failed_harness_build"`).

## `AgentPhaseStats` (dataclass, `library_builder/stats.py`)

Represents one phase's LLM agent involvement (library-build agent or harness-build
agent). One instance is produced per phase, always present in `RunStats` — never
omitted, per FR-009 (identical shape regardless of outcome).

| Field | Type | Notes |
|---|---|---|
| `invoked` | `bool` | Whether an LLM agent was invoked for this phase at all. |
| `duration_seconds` | `float \| None` | The agent invocation's own duration (from `AgentStreamResult.duration_seconds`, threaded through `BuildExplorationResult`/`HarnessExplorationResult`, or through the new `BuildFailureError`/`LLMBudgetError.summary` on the exception path). `None` when `invoked` is `False` — serialized as `"N/A"`. |
| `cost_usd` | `float \| None` | The agent invocation's own cost. `None` when `invoked` is `False`, **or** when the backend never reported a cost figure (e.g. Codex) — both serialize as `"N/A"`; this dataclass does not distinguish "not invoked" from "cost unknown" because FR-006 requires both to read `"N/A"`. |
| `summary` | `str` | One of: the agent's own final natural-language message (Research decision 2), the literal string `"N/A"` when `invoked` is `False`, or the literal string `"unavailable"` when `invoked` is `True` but no final message was captured (FR-006b). Always a concrete string — never `None` — since the three cases are already distinguished before construction. |

Constructed from an existing typed result via a single conversion function per phase
(e.g. `_agent_stats_from_build(result: BuildExplorationResult) -> AgentPhaseStats`,
`_agent_stats_from_harness(result: HarnessExplorationResult) -> AgentPhaseStats`), plus
a variant that constructs it directly from a caught `BuildFailureError`/`LLMBudgetError`
when the phase raised instead of returning.

## `RunStats` (dataclass, `library_builder/stats.py`)

The single record written once per `harnessbuddy generate` invocation.

| Field | Type | Notes |
|---|---|---|
| `total_duration_seconds` | `float` | Wall-clock time from the start of `_cmd_generate` to the moment the stats record is finalized (success or failure). Always present — never `"N/A"`, since the run itself always takes some measurable time once it starts. |
| `library_build_agent` | `AgentPhaseStats` | See above. |
| `harness_build_agent` | `AgentPhaseStats` | See above. |
| `status` | `RunStatus` | See above. |

### JSON shape (`stats.json`)

```json
{
  "total_duration_seconds": 42.7,
  "library_build_agent": {
    "invoked": true,
    "duration_seconds": 18.3,
    "cost_usd": 0.0421,
    "summary": "Patched build_library.sh to add the missing -DBUILD_SHARED_LIBS=OFF flag and re-ran the build; install/lib now contains libfoo.a."
  },
  "harness_build_agent": {
    "invoked": false,
    "duration_seconds": "N/A",
    "cost_usd": "N/A",
    "summary": "N/A"
  },
  "status": "success"
}
```

See `contracts/stats-json.md` for the full field contract, including every
invoked/not-invoked/failed combination.

## Field derivation summary (source of truth per field)

| Stats field | Derived from |
|---|---|
| `total_duration_seconds` | A `time.monotonic()` timer started at the top of `_cmd_generate` and read when the run concludes (success return, or one of the `except BuildFailureError`/`except LLMBudgetError` blocks). |
| `library_build_agent.*` | `BuildExplorationResult` returned by `build_library()` when `llm_used=True`; `AgentPhaseStats(invoked=False, ...)` when `llm_used=False`; the `.summary` carried on a caught `BuildFailureError`/`LLMBudgetError` when the library phase raised. |
| `harness_build_agent.*` | Same pattern, from `HarnessExplorationResult` / `build_harness()`. |
| `status` | `SUCCESS` unless the library phase's result is not `succeeded` (or raised) → `FAILED_LIBRARY_BUILD`; else unless the harness phase's result is not `succeeded` (or raised) → `FAILED_HARNESS_BUILD`; else `SUCCESS`. |

## Non-goals for this data model

- No historical/aggregate record across multiple runs — one `RunStats` per invocation,
  one `stats.json` per output directory (Assumptions in spec.md).
- No new status values beyond the three above — an `OutputDirectoryExistsError` raised
  by `_generate_outputs` (existing, unrelated failure mode) is out of scope for this
  feature's status taxonomy; that failure path is unchanged by this feature.
