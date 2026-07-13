# Contract: `agents/scripts/` build verification scripts (extended consumer)

`check_local_build.sh` and `check_docker_build.sh` already exist with the contract spec 009
established (`specs/009-structured-build-environments/contracts/agent-scripts.md`) — their
usage, arguments, and pass/fail semantics are **unchanged** by this feature. What changes is
who calls them: previously only the repair agent; now also HarnessBuddy's own
`OssFuzzExecutor`/`LocalExecutor`, via `subprocess`, as the actual pipeline gate.

## `check_local_build.sh` (unchanged interface)

```
Usage: check_local_build.sh <work_dir>
```

- Called by `LocalExecutor` (new, this feature) as its per-stage pass/fail gate, via
  `environments/verification.py::run_local_verification`.
- Called by the repair agent (unchanged from spec 009) as its own fix-verification step.
- Both call sites pass the same `work_dir`: `.harnessbuddy/<project>/`.

## `check_docker_build.sh` (unchanged interface)

```
Usage: check_docker_build.sh <oss_fuzz_project_dir> <project_name> [harness_name]
```

- Called by `OssFuzzExecutor` (new, this feature) as its atomic pass/fail gate, via
  `environments/verification.py::run_docker_verification`, once after the library-build
  stage (`compile_harnesses.sh` still a stub — research.md #3) and once more after harness
  discovery converges.
- Called by the repair agent (unchanged from spec 009) as its own fix-verification step.
- Both call sites now pass `oss_fuzz_project_dir` = the workspace
  (`.harnessbuddy/<project>/`) during exploration — not a separate, not-yet-created future
  output path (research.md #1, #7). This is the one behavioral fix in this contract: the
  path an agent is told to verify against is guaranteed to exist and be buildable at the
  time it's told to run the command.

## What does NOT change

- Script contents, argument order, exit codes, and stdout/stderr conventions — identical to
  spec 009's contract.
- `agents/library_builder/SKILL.md` / `agents/harness_builder/SKILL.md` step-by-step text —
  still "run the verification command given in the failure context below," unchanged.

## New consumers' failure handling

- When `run_docker_verification`/`run_local_verification` reports `passed=False`,
  `OssFuzzExecutor`/`LocalExecutor` populate `BuildExplorationResult`/
  `HarnessExplorationResult` from the returned `VerificationResult` (data-model.md) exactly
  as they populate them from a direct `RunResult` today — the rest of the pipeline (agent
  fallback, `_validate_install_artifacts`/`_validate_harness_artifacts`, stats) is unaware
  the check ran through a shared script rather than a hand-rolled command.
- A `docker build`/`docker run` invocation failing because Docker itself is unreachable
  (not a build defect) is still distinguished via the existing `_is_environment_unavailable`
  stderr-pattern check (spec 009 research.md #3), applied to `VerificationResult.stderr`
  instead of a raw `RunResult.stderr` — same patterns, same `EnvironmentUnavailableError`
  behavior, no agent fallback (FR-007).
