# Phase 1 Data Model: Structured Build Environments

This feature is a CLI pipeline change, not a data-storage feature — "entities" here are the
typed dataclasses/enums that carry environment selection and per-stage validation state
through `library_builder`, extending the existing model shapes in
`library_builder/models.py` and `library_builder/stats.py` rather than replacing them.

## Environment (new enum)

Location: `library_builder/environments/base.py`

| Value | Meaning |
|---|---|
| `LOCAL` | Stages run as host subprocesses (today's only behavior). |
| `OSS_FUZZ` | Stages run inside the OSS-Fuzz base-builder container. |

Selected once per `generate` invocation (FR-001/FR-002) and used for every stage in that
run — a run does not mix environments across stages.

## EnvironmentExecutor (new protocol)

Location: `library_builder/environments/base.py`

| Member | Type | Notes |
|---|---|---|
| `run_library_build` | `(analysis: AnalysisResult, workdir: Path, *, timeout: int) -> BuildExplorationResult` | Replaces the body of today's `exploration.explore` for the local case; the oss-fuzz case runs the equivalent script inside the container (research.md #1, #4). |
| `run_harness_compile` | `(install_dir: Path, workdir: Path, language: Language, *, extra_include_paths: list[str], extra_library_paths: list[str]) -> HarnessExplorationResult` | Wraps `harness_explorer.explore_harness_compilation`'s retry/parsing loop; only the command-execution step is swapped per environment. |
| `check_availability` | `() -> EnvironmentUnavailableError \| None` | `LocalExecutor` always returns `None`. `OssFuzzExecutor` runs `docker info` (research.md #3). |

Two concrete implementations: `LocalExecutor`, `OssFuzzExecutor`. No plugin registry — a
plain `if environment == Environment.OSS_FUZZ` dispatch in `cli.py`/`exploration.py` picks
the concrete instance, matching FR-013's "no custom environment" scope.

## EnvironmentUnavailableError (new exception)

Location: `library_builder/environments/base.py`

| Field | Type | Notes |
|---|---|---|
| `message` | `str` | Actionable text (e.g. "Docker daemon not reachable: <docker info stderr>"). |
| `environment` | `Environment` | Which environment failed its availability check. |

Distinct from a build/stage failure so the CLI can implement FR-012 (no agent fallback for
this case) with a simple `except EnvironmentUnavailableError` branch ahead of the normal
`BuildFailureError`/`LLMBudgetError` handling already in `cli.py`.

## BuildExplorationResult / HarnessExplorationResult (extended)

Location: `library_builder/models.py` (existing dataclasses)

Add one field to each:

| Field | Type | Default | Notes |
|---|---|---|---|
| `environment` | `Environment` | *(required, no default — always set by the executor that produced the result)* | Recorded so generation, stats, and agent prompts all know which environment validated this result, per FR-006. |

No other fields change shape. `script_path` continues to mean "the exact script text that
was validated and is safe to copy verbatim into generated output" (FR-008) — for the
oss-fuzz executor this is the same `build_library_script(..., oss_fuzz=True)` /
`build_harness_script(..., oss_fuzz=True)` text that was run inside the container, not a
separately-templated copy.

## RunStats (extended)

Location: `library_builder/stats.py`

| Field | Type | Notes |
|---|---|---|
| `environment` | `str` (the `Environment` value) | Persisted to `stats.json` alongside existing `library_build_agent`/`harness_build_agent`/`status` fields, satisfying FR-006's "report ... as part of the run's final report and run statistics." |

`RunStatus` (`SUCCESS`, `FAILED_LIBRARY_BUILD`, `FAILED_HARNESS_BUILD`) is unchanged —
combined with the new `environment` field it already answers "which stage failed, in which
environment" (FR-007), since a run only ever has one environment.

## DependencyState (unchanged shape, new consumer)

Location: `library_builder/dependency_resolution.py`

No field changes. The oss-fuzz executor's probe-image bootstrap (research.md #2) reads
`state.apt_packages` to build the probe image, and a rebuild is triggered whenever that list
changes across an agent-repair iteration — this is a new *read* of existing state, not a new
persisted field.

## Validation rules carried over unchanged

- `_validate_install_artifacts` (install/lib/*.a, install/include/* non-empty) — reused by
  both executors after their respective `run_library_build`.
- `_validate_harness_artifacts` (out/ non-empty) — reused by both executors after their
  respective `run_harness_compile`.

Re-running the *same* validation regardless of environment is intentional (Constitution
Principle VI: an agent's or script's self-reported success is never the final verdict).
