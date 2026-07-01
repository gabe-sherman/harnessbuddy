# Contract: `stats.json`

## Location

Written once per `harnessbuddy generate` invocation, at the parent output directory
returned by `_resolve_output_paths()` — the directory that directly contains the
`local/` and `oss-fuzz/` subdirectories:

```
<output>/<project_name>/output/stats.json
<output>/<project_name>/output/local/
<output>/<project_name>/output/oss-fuzz/
```

Not duplicated into `local/` or `oss-fuzz/`.

## Presence

- Written whenever the run reaches the point of establishing the output directory
  (immediately after `_resolve_output_paths()`), regardless of whether the run
  subsequently succeeds or fails during either build phase.
- Not written when the run fails before that point (repository ingestion failure,
  unsupported-repository/analysis failure) — there is no output directory to write into.
- Overwritten (not appended) whenever a run reuses the same output directory, matching
  the existing overwrite-the-whole-directory behavior in `_resolve_output_paths()`.

## Schema

```json
{
  "total_duration_seconds": <number>,
  "library_build_agent": {
    "invoked": <boolean>,
    "duration_seconds": <number> | "N/A",
    "cost_usd": <number> | "N/A",
    "summary": <string>
  },
  "harness_build_agent": {
    "invoked": <boolean>,
    "duration_seconds": <number> | "N/A",
    "cost_usd": <number> | "N/A",
    "summary": <string>
  },
  "status": "success" | "failed_library_build" | "failed_harness_build"
}
```

Field names, nesting, and types are identical across every run (FR-009) — a consumer
never needs to branch on which fields are present, only on the string/number union for
`duration_seconds`/`cost_usd` and the three literal values of `status`.

### Field contract details

- `total_duration_seconds`: always a number (seconds, floating point). Never `"N/A"`.
- `*_agent.invoked`: `true` only when that phase actually spawned an LLM agent process
  (i.e. `--agent` was supplied and the deterministic attempt for that phase failed).
- `*_agent.duration_seconds` / `*_agent.cost_usd`: the literal string `"N/A"` when
  `invoked` is `false`. When `invoked` is `true`, `duration_seconds` is always a number;
  `cost_usd` is the literal string `"N/A"` if the agent backend didn't report a cost
  figure (e.g. Codex), otherwise a number (US dollars).
- `*_agent.summary`: the literal string `"N/A"` when `invoked` is `false`; the literal
  string `"unavailable"` when `invoked` is `true` but the agent never produced a final
  natural-language message before exiting; otherwise the agent's own final message,
  verbatim (not truncated, not re-summarized).
- `status`: exactly one of the three literal values shown above.

## Example: clean success, no agents invoked

```json
{
  "total_duration_seconds": 12.4,
  "library_build_agent": {
    "invoked": false,
    "duration_seconds": "N/A",
    "cost_usd": "N/A",
    "summary": "N/A"
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

## Example: library agent repaired the build, harness needed no help

```json
{
  "total_duration_seconds": 96.1,
  "library_build_agent": {
    "invoked": true,
    "duration_seconds": 71.8,
    "cost_usd": 0.0913,
    "summary": "Added -DBUILD_SHARED_LIBS=OFF to the CMake invocation; install/lib/libfoo.a is now produced."
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

## Example: harness build unrecoverable (stub output still produced)

```json
{
  "total_duration_seconds": 143.5,
  "library_build_agent": {
    "invoked": false,
    "duration_seconds": "N/A",
    "cost_usd": "N/A",
    "summary": "N/A"
  },
  "harness_build_agent": {
    "invoked": true,
    "duration_seconds": 88.2,
    "cost_usd": "N/A",
    "summary": "unavailable"
  },
  "status": "failed_harness_build"
}
```

(Here the harness agent ran via Codex, which never reports `cost_usd`, and crashed
before emitting a final `agent_message`, hence `"unavailable"`.)
