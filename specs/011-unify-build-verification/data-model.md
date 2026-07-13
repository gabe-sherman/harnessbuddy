# Phase 1 Data Model: Unified Build Verification

This feature is a pipeline/orchestration change, not a data-storage feature — "entities"
here are the typed dataclasses/modules that carry the workspace-as-real-project and
shared-verification-script concepts through `library_builder`, extending the existing shapes
in `models.py`/`stats.py` rather than replacing them.

## Environment, EnvironmentExecutor, EnvironmentUnavailableError (unchanged)

Location: `library_builder/environments/base.py`. No field or method signature changes.
`OssFuzzExecutor`/`LocalExecutor`'s *internal* implementation of `run_library_build`/
`run_harness_compile` changes (Research #1–#3); the protocol they implement does not.

## ProjectWorkspace writers (new module)

Location: `library_builder/oss_fuzz/workspace.py`

Extracted from `oss_fuzz/generation.py`'s existing `_write_project_yaml`/`_write_dockerfile`/
`_write_build_sh` (content unchanged), so the same writer functions can run twice in a run's
lifecycle instead of once:

| Function | Called from | Notes |
|---|---|---|
| `write_project_yaml(workspace, analysis) -> Path` | `OssFuzzExecutor.run_library_build` (first call in a run) | Same content as today's `_write_project_yaml`. |
| `write_dockerfile(workspace, analysis, *, include_bear: bool) -> Path` | `OssFuzzExecutor` (early, and again if the apt-package set changes) | `include_bear=True` for the workspace's live copy (Research #5); final generation calls it again with `include_bear=False` to produce the shipped variant. |
| `write_build_sh(workspace) -> Path` | `OssFuzzExecutor.run_library_build` (first call) | Same two-line orchestrator content as today's `_write_build_sh`. |

These functions are idempotent (safe to call again with the same inputs) and are the single
place `Dockerfile`/`build.sh`/`project.yaml` content is defined — both early materialization
and final generation call them, rather than final generation re-implementing equivalent
content separately (Constitution Principle V).

## VerificationResult (new dataclass)

Location: `library_builder/environments/verification.py`

| Field | Type | Notes |
|---|---|---|
| `passed` | `bool` | The shared script's exit code was 0. |
| `command` | `list[str]` | The literal argv invoked (e.g. `["bash", ".../check_docker_build.sh", "<workspace>", "<project>"]`), recorded for FR-010 ("a run's report/logs MUST record the literal verification command"). |
| `stdout` | `str` | Combined stdout from the script. |
| `stderr` | `str` | Combined stderr from the script. |
| `duration_seconds` | `float` | Wall-clock time for this invocation. |

Produced by two new thin functions, both wrapping `core.subprocesses.run_command_streaming`:

| Function | Wraps |
|---|---|
| `run_docker_verification(workspace: Path, project_name: str) -> VerificationResult` | `bash agents/scripts/check_docker_build.sh <workspace> <project_name>` |
| `run_local_verification(workspace: Path) -> VerificationResult` | `bash agents/scripts/check_local_build.sh <workspace>` |

`OssFuzzExecutor`/`LocalExecutor` call these directly instead of hand-rolling equivalent
`docker build`/`docker run` or `bash build_library.sh && bash compile_harnesses.sh`
sequences — this is the concrete mechanism behind FR-001 ("exactly one verification script
... used identically by HarnessBuddy's own pipeline and by the repair agent"): the same
`agents/scripts/*.sh` file both invocations run is *the* implementation, not a
Python-side re-implementation kept "consistent" with it by convention.

## BuildExplorationResult / HarnessExplorationResult (unchanged shape, new provenance)

Location: `library_builder/models.py`

No new fields. `succeeded`/`stdout`/`stderr`/`exit_code`/`duration_seconds` are now
populated from a `VerificationResult` (for the executor's final per-stage gate) rather than
directly from `run_command_streaming`'s `RunResult` — same field types, different origin.
`script_path` keeps meaning "the exact validated script, safe to copy verbatim" (unchanged);
for the oss-fuzz environment it now always points into the workspace, which is also the
directory `VerificationResult.command` references, so the two are guaranteed consistent by
construction rather than by separate bookkeeping.

## GenerationResult (unchanged shape, new construction)

Location: `library_builder/models.py`. No field changes.
`generate_oss_fuzz`/`generate_local` populate `files` by copying already-validated workspace
files (Research #6) instead of writing fresh content for most entries; `project.yaml` and
the bear-stripped `Dockerfile` remain derived (via the `ProjectWorkspace` writers above), not
copied, since they must differ from the workspace's live versions.

## agents.py: `_verification_command` (simplified signature)

Location: `library_builder/agents.py`

`oss_fuzz_project_dir: Path | None` is removed from `_verification_command`,
`build_library_prompt`, `build_harness_prompt`, and their `cli.py` call sites
(`build_library`, `build_harness`, `_run_library_phase`, `_run_harness_phase`). The
oss-fuzz branch uses `workdir` directly (Research #7) — the same parameter every other
branch already uses.

## RunStats (unchanged shape)

Location: `library_builder/stats.py`. No new fields required by this feature; the existing
`environment` field (spec 009) and `compile_commands_path` field (spec 010) already cover
what SC-001/SC-005 need to verify. `stats.json` gains no new keys.

## Validation rules carried over unchanged

- `_validate_install_artifacts` (install/lib/*.a, install/include/* non-empty) and
  `_validate_harness_artifacts` (out/ non-empty) remain the independent, typed
  re-verification Constitution Principle VI requires — a passing `VerificationResult` alone
  is necessary but not sufficient; these checks still run against the workspace's `install/`
  and `out/` directories afterward, exactly as today.
