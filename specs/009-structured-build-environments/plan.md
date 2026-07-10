# Implementation Plan: Structured Build Environments

**Branch**: `main` (no feature branch created — no git hook configured) | **Date**: 2026-07-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/009-structured-build-environments/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Today, `harnessbuddy generate` always builds and validates the library and harness on the
local host (`library_builder/exploration.py`, `library_builder/harness_explorer.py`), then
copies the validated scripts verbatim into both `local/` and `oss-fuzz/` output — trusting
that a script proven correct against the host's toolchain also works inside the OSS-Fuzz
container it's pasted into. This plan adds an explicit `--environment {local,oss-fuzz}`
choice (default `local`, preserving current behavior) and moves stage validation for the
oss-fuzz environment into the container itself: each stage (library build, then harness
compile) runs and is validated with `docker run` against the real
`gcr.io/oss-fuzz-base/base-builder` image, sharing state across the two stages via a
bind-mounted workdir instead of a long-lived container, before final project generation.
The LLM repair agents keep editing files on the host but now verify fixes by invoking
environment-appropriate scripts under `agents/scripts/` — which are corrected to match the
real generated script names/interfaces (they currently reference nonexistent
`build_lib.sh`/`build_harness.sh`/`default_harness` and an undefined `$target_dir`).

## Technical Context

**Language/Version**: Python 3.13 (existing codebase; no version change)

**Primary Dependencies**: Standard library `subprocess` only — the oss-fuzz environment
shells out to the `docker` CLI the same way `tests/run_ground_truth.py` already does
(`docker build`, `docker run --entrypoint bash ... -c "..."`). No new Python dependency.

**Storage**: Repo-local `.harnessbuddy/<project>/` workspace and `state.json`, unchanged in
kind; `state.json` and `stats.json` gain an `environment` field.

**Testing**: `pytest`. Local-environment tests continue mocking only the subprocess
boundary. New oss-fuzz-environment tests are gated behind `HARNESSBUDDY_RUN_DOCKER=1` and
skipped otherwise, per constitution Principle IV — mirroring how `run_ground_truth.py`
already isolates its Docker dependency from the default test run.

**Target Platform**: Local development hosts (macOS/Linux) running `harnessbuddy generate`;
selecting the oss-fuzz environment additionally requires a working Docker daemon on that
host.

**Project Type**: Single CLI project (`src/harnessbuddy/`) — no new top-level project.

**Performance Goals**: None beyond "acceptable for a dev-loop CLI." The oss-fuzz
environment is expected to be slower per run than local (container build/run overhead);
this is an accepted tradeoff for correctness, not a regression to fix.

**Constraints**: No new runtime dependency for container orchestration. Environment
selection must not change import-time behavior (Constitution II: imports stay
side-effect-free). The executor abstraction must cover exactly the two environments in
FR-001/FR-013 — no generic plugin/"custom environment" framework.

**Scale/Scope**: Two selectable environments (local, oss-fuzz) for the `generate` command;
custom environments explicitly out of scope (FR-013).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Code Quality**: No known conflict. New modules (environment executors) will carry
  type annotations and stay under the 100-line/complexity-8 function limits; the two
  executors are small enough that this is mechanical, not a design risk.
- **II. Modular Package Boundaries**: The environment executor abstraction is
  library_builder-specific (it produces `BuildExplorationResult`/`HarnessExplorationResult`,
  which are library_builder types), so it lives under
  `library_builder/environments/`, not `core/`. `core.subprocesses.run_command_streaming`
  remains the single generic subprocess primitive both executors call — the docker CLI
  command construction is library_builder-specific and does not belong in `core`. PASS.
- **III. Extensible Multi-Tool Architecture**: No new tool is introduced; this stays
  entirely inside `library_builder`. PASS.
- **IV. Test-First, Behavior-Focused Testing**: The oss-fuzz executor's Docker calls are a
  genuine external boundary (Docker daemon) — local-environment tests keep mocking
  `run_command_streaming`/`run_command`; new oss-fuzz-environment tests are gated behind
  `HARNESSBUDDY_RUN_DOCKER=1` and do not run by default. PASS, pending Phase 1 test list.
- **V. Simplicity, No Speculative Features**: FR-013 explicitly drops "custom" environment
  scope; the executor abstraction is sized for exactly two concrete implementations, not a
  registry/plugin system. `--skip-validation` is reused rather than adding a third flag.
  PASS.
- **VI. Structured, Guardrailed Agent Invocation**: The repair agent's verification step
  (FR-009) reuses the same typed `BuildExplorationResult`/`HarnessExplorationResult`
  re-validation already in `agents.py` (`_validate_install_artifacts`,
  `_validate_harness_artifacts`) — now run through the selected environment's executor —
  rather than trusting the agent's own exit code or the check-script's exit code alone.
  PASS.

No violations. Complexity Tracking table is not needed.

## Project Structure

### Documentation (this feature)

```text
specs/009-structured-build-environments/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   ├── cli.md
│   └── agent-scripts.md
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/harnessbuddy/
├── cli.py                                  # + --environment flag, threads Environment through
├── core/
│   └── subprocesses.py                     # unchanged — still the one generic executor primitive
├── library_builder/
│   ├── models.py                           # + Environment enum, + environment field on results
│   ├── environments/                       # NEW package
│   │   ├── __init__.py
│   │   ├── base.py                         # Environment enum, EnvironmentExecutor protocol,
│   │   │                                   # EnvironmentUnavailableError
│   │   ├── local.py                        # host-subprocess executor (today's behavior, moved)
│   │   └── oss_fuzz.py                     # docker-based executor (bind-mount, per-stage docker run)
│   ├── exploration.py                      # delegates command execution to an EnvironmentExecutor
│   ├── harness_explorer.py                 # delegates command execution to an EnvironmentExecutor
│   ├── agents.py                           # prompts include environment-specific verify command
│   ├── stats.py                            # RunStats gains an environment field
│   └── dependency_resolution.py            # unchanged
agents/
├── library_builder/SKILL.md                # step 7 references check_<environment>_build.sh
├── harness_builder/SKILL.md                 # step 6 references check_<environment>_build.sh
└── scripts/
    ├── check_local_build.sh                # FIXED: correct args/script names (FR-010)
    └── check_docker_build.sh               # FIXED: correct docker invocation (FR-010)

tests/
├── test_cli.py                             # + --environment parsing/dispatch tests
└── library_builder/
    ├── environments/                       # NEW
    │   ├── test_local.py
    │   └── test_oss_fuzz.py                # HARNESSBUDDY_RUN_DOCKER=1-gated
    ├── test_exploration.py                 # updated for executor delegation
    ├── test_harness_explorer.py            # updated for executor delegation
    └── test_agents.py                      # + environment-specific prompt assertions
```

**Structure Decision**: Single project, existing `src/harnessbuddy/library_builder/`
layout. Add one new subpackage, `library_builder/environments/`, holding the environment
abstraction and both concrete executors. No new top-level package, no changes to `core/`
beyond continuing to reuse its existing subprocess primitives.

## Complexity Tracking

*No Constitution Check violations — table intentionally omitted.*
