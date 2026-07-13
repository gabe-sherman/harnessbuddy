# Implementation Plan: Unified Build Verification

**Branch**: `main` (no feature branch created — no git hook configured) | **Date**: 2026-07-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/011-unify-build-verification/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Today, `OssFuzzExecutor` (`library_builder/environments/oss_fuzz.py`) validates a build by
building a throwaway "probe" image (`base-builder` + apt packages, no `git clone`, no
`COPY`) and running `build_library.sh`/`compile_harnesses.sh` as two separate `docker run`
calls against it — never the project's actual `Dockerfile`/`build.sh`. Concretely, this
means the `oss_fuzz_project_dir` path already threaded into the repair agent's prompt
(`agents.py::_verification_command`) during exploration points at a directory that doesn't
exist yet (`generate_oss_fuzz()` only creates it after exploration finishes), so an agent
told to run `check_docker_build.sh <oss_fuzz_project_dir> <project_name>` mid-run today
would `cd` into nothing.

This plan makes the exploration-time workspace (`.harnessbuddy/<project>/`) *become* the
real OSS-Fuzz project directory as soon as its pieces are known — `Dockerfile` (with the
real `git clone`, not a bind mount), `build.sh`, `project.yaml`, `harness_source/` — instead
of a separate synthetic representation. Harness link-flag discovery
(`harness_explorer.py`, up to 5 retries) keeps a fast, direct-exec path against the
already-built image for its internal iteration (no full `compile` per attempt — this is
pure probing, not the verification gate). Once a stage's script converges, both
`OssFuzzExecutor`/`LocalExecutor` and the repair agent confirm it by literally invoking the
same script — `agents/scripts/check_docker_build.sh` / `check_local_build.sh` — the
Python code via `subprocess`, the agent via its own shell tool. Final generation stops
re-deriving `Dockerfile`/`build.sh`/`build_library.sh`/`compile_harnesses.sh` from templates
and instead copies the already-validated files out of the workspace into `local/`/`oss-fuzz/`,
adding only what's genuinely output-only (`setup.sh` for local; nothing extra for oss-fuzz
beyond the bear-instrumentation strip already planned in spec 010).

## Technical Context

**Language/Version**: Python 3.13 (existing codebase; no version change)

**Primary Dependencies**: Standard library `subprocess` only. `docker` and `bash` CLIs,
invoked exactly as `agents/scripts/check_docker_build.sh`/`check_local_build.sh` already
define — no new runtime dependency.

**Storage**: `.harnessbuddy/<project>/` gains a persistent role: it is no longer scratch
space translated into a differently-shaped output at the end, it *is* the project
directory (superset: also holds `src/`, `build/`, `install/`, `out/`, `state.json`, which
are exploration-only and excluded when copying to final output).

**Testing**: `pytest`. Existing `HARNESSBUDDY_RUN_DOCKER=1` gating for Docker-dependent
tests is unchanged (Constitution Principle IV). New tests cover: the workspace containing a
real `Dockerfile`/`build.sh` mid-run, `OssFuzzExecutor`/`LocalExecutor` invoking the shared
scripts via subprocess (mocked at that boundary for non-Docker tests), and final generation
copying rather than re-templating already-validated files.

**Target Platform**: Local development hosts (macOS/Linux) running `harnessbuddy generate`;
oss-fuzz environment additionally requires a working Docker daemon, unchanged from today.

**Project Type**: Single CLI project (`src/harnessbuddy/`) — no new top-level project.

**Performance Goals**: The oss-fuzz environment's final atomic check (`docker build` +
`compile`) is expected to be slower per run than per-stage `docker run` calls were, but
Docker layer caching (stable `git clone`/`apt-get install` layers, changing only the final
`COPY` layers) keeps repeat builds within the same run fast. This is an accepted tradeoff
for correctness (real image, real entrypoint), not a regression to fix.

**Constraints**: No new runtime dependency. Harness-dependency discovery's internal retry
loop must not be forced through a full `docker build` per attempt (FR-011) — it keeps
reusing the already-built image directly. compile_commands.json capture (bear/CMake-flag
instrumentation, spec 010) must keep working unchanged against the now-real Dockerfile: the
workspace's "live" Dockerfile still unconditionally provisions `bear`; the copy written to
final `oss-fuzz/` output still excludes it, exactly as `oss_fuzz/generation.py::_write_dockerfile`
does today.

**Scale/Scope**: Two environments (local, oss-fuzz), same as spec 009 — no new environment
type introduced.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Code Quality**: No known conflict. Replacing `_ensure_probe_image`'s tempdir
  Dockerfile with workspace materialization functions and a subprocess call to the shared
  scripts is a similarly small, typed surface — no function is expected to approach the
  100-line/complexity-8 limits.
- **II. Modular Package Boundaries**: Workspace materialization (writing `Dockerfile`,
  `build.sh`, `project.yaml`, `harness_source/` into the workspace) is
  `library_builder`-specific output-shaping logic, so it lives under
  `library_builder/oss_fuzz/` (reusing/extending `generation.py`'s existing writers) and
  `library_builder/environments/oss_fuzz.py` — not `core/`. The shared verification
  scripts remain plain files under `agents/scripts/`, invoked via
  `core.subprocesses.run_command`/`run_command_streaming`, the same generic subprocess
  primitive both executors already use. PASS.
- **III. Extensible Multi-Tool Architecture**: No new tool; stays inside `library_builder`.
  PASS.
- **IV. Test-First, Behavior-Focused Testing**: `docker build`/`docker run` remain the
  genuine external boundary — tests continue mocking `run_command`/`run_command_streaming`
  for non-Docker runs and gate real-Docker tests behind `HARNESSBUDDY_RUN_DOCKER=1`,
  unchanged from spec 009. PASS.
- **V. Simplicity, No Speculative Features**: This plan *removes* a code path
  (`_ProbeImageBuildError`/`_ensure_probe_image`'s synthetic tempdir Dockerfile,
  `generate_oss_fuzz`'s from-scratch re-templating of already-validated scripts) rather than
  adding one, per "replace, don't deprecate." No new CLI flag — reuses `--environment` and
  `--skip-validation` exactly as spec 009 defined them. PASS.
- **VI. Structured, Guardrailed Agent Invocation**: The repair agent's verification command
  (FR-006) is unchanged in kind — still a single documented shell command appended to a
  typed-context prompt — except it now resolves to a real, existing directory during
  exploration (fixing today's dangling-path gap) instead of only after generation. The
  deterministic re-validation of agent output (`_validate_install_artifacts`,
  `_validate_harness_artifacts`) is unchanged; the atomic script's own exit code is still
  cross-checked against those artifact checks, not trusted alone. PASS.

No violations. Complexity Tracking table is not needed.

**Post-Phase-1 re-check**: Design artifacts (research.md, data-model.md, contracts/,
quickstart.md) introduce one new module (`oss_fuzz/workspace.py`, extracted writer
functions) and one new thin wrapper module (`environments/verification.py`), both small and
single-purpose, and remove two code paths (`_ProbeImageBuildError`/`_ensure_probe_image`,
template-based re-derivation in `generate_oss_fuzz`/`generate_local`) rather than adding
parallel ones. All six gates above still PASS; no new violations introduced by the design.

## Project Structure

### Documentation (this feature)

```text
specs/011-unify-build-verification/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/harnessbuddy/
├── cli.py                                    # oss_output_path becomes "the workspace" during
│                                              # exploration; _generate_outputs copies validated
│                                              # files instead of calling per-file writers
├── library_builder/
│   ├── environments/
│   │   ├── base.py                           # unchanged (Environment, EnvironmentExecutor, error)
│   │   ├── local.py                          # run_library_build/run_harness_compile now finish
│   │   │                                     # by invoking check_local_build.sh via subprocess
│   │   └── oss_fuzz.py                       # probe-image bootstrap replaced by real workspace
│   │                                          # Dockerfile; final check via check_docker_build.sh
│   ├── exploration.py                        # writes build_library.sh into the real workspace
│   │                                          # layout; no behavior change to the build logic itself
│   ├── harness_explorer.py                   # discovery loop keeps its fast direct-exec path;
│   │                                          # unchanged retry/parsing logic
│   ├── oss_fuzz/
│   │   ├── generation.py                     # Dockerfile/build.sh/project.yaml writers reused
│   │   │                                      # for early workspace materialization, not just
│   │   │                                      # final output; final generation copies files
│   │   └── workspace.py                      # NEW: shared "materialize/refresh project layout
│   │                                          # in workspace" functions, called by both the
│   │                                          # executor (early/incrementally) and generation.py
│   │                                          # (selects which files ship)
│   └── local/generation.py                   # final generation copies files; adds setup.sh only
└── agents.py                                  # _verification_command simplifies now that
                                                # oss_fuzz_project_dir is always the workspace
                                                # during exploration (no separate future path)

agents/scripts/
├── check_local_build.sh                      # unchanged contract; now also invoked by
│                                              # LocalExecutor itself, not only the agent
└── check_docker_build.sh                     # unchanged contract; now also invoked by
                                               # OssFuzzExecutor itself, not only the agent

tests/
├── library_builder/environments/test_oss_fuzz.py   # workspace-is-real-project assertions
├── library_builder/oss_fuzz/test_generation.py      # copy-not-regenerate assertions
├── library_builder/local/test_generation.py         # copy-not-regenerate assertions
└── agents/test_check_scripts.py                     # unchanged contract, exercised from both
                                                       # the executor and the agent path
```

**Structure Decision**: Single existing CLI project (`src/harnessbuddy/`), no new top-level
package. The only new module is `library_builder/oss_fuzz/workspace.py`, holding the
Dockerfile/build.sh/project.yaml materialization logic shared between early-exploration
workspace setup and final generation's file selection — avoiding a duplicated "write these
files" implementation in two places (Constitution Principle V: prefer one implementation
over two once the same pattern would otherwise be written twice).
