# Data Model: Clear Build Logging and Diagnostics

This feature adds an internal reporting/diagnostics model, not persisted
application data. The entities below live in the new `core/reporting.py`
module and are consumed by `cli.py`; none are new database or wire schemas.

## `Phase` (enum)

Ordered, fixed set of pipeline stages a `generate` run passes through.
Matches the phase list in `spec.md`'s Key Entities and `research.md` Decision
4.

| Member | Console label | Corresponds to (existing code) |
|---|---|---|
| `INGESTION` | "Repository ingestion" | `cli.py:_ingest_source` |
| `STATIC_ANALYSIS` | "Static analysis" | `library_builder/analysis.py:analyze()` |
| `STATIC_LIBRARY_BUILD` | "Static library build" | `cli.py:build_library()` → `executor.run_library_build()` |
| `AGENT_LIBRARY_REPAIR` | "Agent-assisted library repair" | `agents.py:invoke_library_builder_agent()` |
| `HARNESS_COMPILE_PROBE` | "Harness compile probe" | `cli.py:build_harness()` → `executor.run_harness_compile()` |
| `AGENT_HARNESS_REPAIR` | "Agent-assisted harness repair" | `agents.py:invoke_harness_builder_agent()` |
| `OUTPUT_GENERATION` | "Output generation" | `cli.py:_generate_outputs` |

**Validation rule**: `AGENT_LIBRARY_REPAIR` only ever occurs immediately after
a failed `STATIC_LIBRARY_BUILD` in the same run; `AGENT_HARNESS_REPAIR` only
ever occurs immediately after a failed `HARNESS_COMPILE_PROBE`. The reporter
does not enforce this (it reflects, not drives, control flow already in
`cli.py`), but console output and tests rely on this ordering per spec FR-007.

## `PhaseExecution`

One instance per phase actually run in a given `generate` invocation.

| Field | Type | Notes |
|---|---|---|
| `phase` | `Phase` | which phase this is |
| `status` | `"running" \| "succeeded" \| "failed"` | current/final state |
| `started_at` | `float` (monotonic seconds) | for elapsed-time display; not wall-clock |
| `ended_at` | `float \| None` | `None` while `status == "running"` |
| `log_path` | `Path \| None` | set once the phase has produced subprocess output; `.harnessbuddy/<project>/logs/<phase>.log` |

**State transitions**: `running → succeeded` or `running → failed`. No other
transitions are valid; a `PhaseExecution` is immutable once it leaves
`running`.

## `FailureDiagnostic`

Built only when a `PhaseExecution` ends with `status == "failed"`.

| Field | Type | Notes |
|---|---|---|
| `phase` | `Phase` | which phase failed |
| `step` | `str` | short, human-readable name of the specific failing step within the phase (e.g. "cmake configure", "harness compile", "LLM repair attempt"); derived from the source exception/result, not free text invented at print time |
| `message` | `str` | one- or two-line human-readable description of what went wrong |
| `origin` | `"deterministic" \| "agent"` | satisfies FR-006 — lets the console distinguish a static-step failure from a failed repair attempt |
| `log_path` | `Path \| None` | full raw output location for this phase, per Decision 3 in research.md; `None` only if the phase failed before any subprocess ran (e.g. repo not found) |
| `exit_code` | `int \| None` | from the underlying result dataclass, when applicable |

**Construction rule**: A `FailureDiagnostic` is always built by reading an
existing result object (`BuildExplorationResult`, `HarnessExplorationResult`)
or existing exception (`BuildFailureError`, `UnsupportedRepositoryError`,
`EnvironmentUnavailableError`, `OutputDirectoryExistsError`, etc.) — it never
independently re-derives success/failure. This is what keeps it consistent
with `check_local_build.sh`/`check_docker_build.sh` as the single definition
of "the build passed" (Constitution Principle III).

## `RunReport`

Aggregates every `PhaseExecution` (and its `FailureDiagnostic`, if any) for
one `generate` invocation, in the order phases ran.

| Field | Type | Notes |
|---|---|---|
| `phases` | `list[PhaseExecution]` | in execution order; satisfies FR-007 (ordering of multiple failures) and SC-001 (full phase sequence reviewable after the run) |
| `diagnostics` | `list[FailureDiagnostic]` | in the order the corresponding phases failed |

`RunReport` is printed incrementally (each `PhaseExecution` as it starts/ends)
rather than held back to the end; it is not itself persisted to disk — the
per-phase `log_path` files and the existing `stats.py` run-stats output
(`RunStatus`, `AgentPhaseStats`) remain the persisted records. `RunReport` is
an in-memory convenience for ordering console output and tests, not a new
on-disk artifact.

**Note (see research.md addendum)**: under `--skip-validation`, a
`PhaseExecution` can end `failed` while the run still proceeds into later
phases — including when a library-build agent's stop-for-human/budget-limited
error is converted into a synthetic failed result. So `diagnostics` ordering
(FR-007) must support failures that span across phases (e.g. library phase
fails, run continues, harness phase separately fails), not only the
within-phase static-build-then-its-own-repair chain the original design
anticipated. Exactly one `FailureDiagnostic` must still be produced per
failed phase even when multiple existing call sites currently print about the
same underlying failure (see research.md addendum's duplicate-print example).

## Debug mode

Not a data entity — a single `bool` derived from the existing `--log-level`
flag's value (`True` when `--log-level debug`, `False` for every other
choice or the default) threaded into the reporter at construction time. The
flag itself keeps its five choices (see research.md Decision 2); only this
derived boolean feeds the reporter. **Revised**: this bool no longer
controls whether `run_command_streaming` prints live (that's now the
default, gated by Quiet mode below, not by debug) — it controls only (a)
whether a failed phase's diagnostic block repeats the full raw output
inline, and (b) whether Python's internal `logging` level is `DEBUG`. See
research.md Decision 5 (revised) and Decision 2 (revised).

## Quiet mode

Not a data entity — a single `bool` from a new `--quiet` store-true flag,
independent of both `--log-level` and each other Phase/FailureDiagnostic
field, threaded into `PhaseReporter`/`run_command_streaming` at construction
time. When `True`, `run_command_streaming` does not print each line live as
it runs (it still always writes the full output to the phase's `log_path`,
per FR-004, and still returns the same `RunResult`). Phase start/end banners
and failure diagnostics are unaffected by this flag — only per-line raw
streaming is. Orthogonal to Debug mode: a run can be quiet-and-debug (no live
streaming, but a failure's raw output still appears once, inline with its
diagnostic), verbose-and-debug (live streaming plus a repeated inline copy on
failure), quiet-and-not-debug (silent phases, log-file-only raw output), or
the new default of verbose-and-not-debug (today's full streaming, now
bracketed by phase banners).
