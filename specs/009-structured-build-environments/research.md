# Phase 0 Research: Structured Build Environments

## 1. How to validate each stage inside the OSS-Fuzz container without the atomic `compile` entrypoint

**Decision**: Run each stage as its own `docker run --rm --entrypoint bash <image> -c "bash <script>.sh"`
invocation, with the project's `.harnessbuddy/<project>/` workdir bind-mounted into the
container so state (`install/`, then `out/`) persists across the two separate `docker run`
calls. No long-lived container, no `docker commit` between stages.

**Rationale**: `tests/run_ground_truth.py::_docker_build_and_compile` already proves the
working invocation shape for this codebase: `docker run -e ... --rm --entrypoint bash <tag>
-c "compile && ..."`. That script validates the whole project atomically through the
base-builder's `compile` entrypoint (which runs `build.sh` — both stages — in one shot).
This feature needs per-stage granularity *before* `build.sh`/`Dockerfile` exist in final
form, so it bypasses `compile` and calls `build_library.sh` then `compile_harnesses.sh`
directly, using the same `--entrypoint bash -c "..."` pattern. A bind mount is simpler and
more debuggable than a long-lived container + `docker exec`: it reuses the exact
`workdir`-relative path convention `exploration.py`/`harness_explorer.py` already use for
the local environment, and each stage is an independent, inspectable `docker run` that
either succeeds or leaves its output on the host filesystem for diagnosis.

**Alternatives considered**:
- *Long-lived container + `docker exec` per stage*: keeps one container process alive
  across stages, avoiding a second `docker run` startup. Rejected: adds container
  lifecycle management (start, exec, stop, cleanup-on-crash) for a marginal startup-time
  win, and complicates FR-007's "diagnostic output sufficient to diagnose without
  re-running" since a crashed long-lived container can leave stale state.
- `docker commit` *between stages*: snapshot the container's filesystem after stage 1 and
  run stage 2 from that image. Rejected: `docker commit` is comparatively slow, produces
  throwaway images that need explicit cleanup, and duplicates what a bind mount gives for
  free.
- *Route everything through `compile`*: keep using the atomic entrypoint and only gate on
  the combined result. Rejected outright by the spec — this is exactly the "post-build,
  not during-build" pattern User Story 2 asks to replace; a combined failure can't say
  which stage failed.

## 2. Base image and package bootstrap for the oss-fuzz environment during exploration

**Decision**: Build a small, run-scoped probe image tagged
`harnessbuddy-probe/<project_name>:latest` from `gcr.io/oss-fuzz-base/base-builder`, with
`RUN apt-get install -y --no-install-recommends <accumulated apt packages>` and the repo
cloned at `repo_ref`, matching the same shape the final `oss-fuzz/Dockerfile` will have.
Rebuild the probe image only when the accumulated apt-package set changes (i.e. after an
agent reports new packages via `agent_report.json`), not on every stage.

**Rationale**: The oss-fuzz executor needs *some* image with the right compiler toolchain,
sanitizer defaults, and system packages before it can run `build_library.sh` in-container.
Building this probe image from the exact same `gcr.io/oss-fuzz-base/base-builder` base and
apt-package list that `oss_fuzz/generation.py`'s `_write_dockerfile` already produces means
the exploration-time image and the final generated Dockerfile agree by construction — no
separate translation step to drift.

**Alternatives considered**:
- *Plain `gcr.io/oss-fuzz-base/base-builder` with no packages, installing packages inline
  in the stage command*: avoids an image build step. Rejected: reinstalling apt packages on
  every `docker run` wastes time on every stage/retry and doesn't match the final
  Dockerfile's `RUN apt-get install` layer, reintroducing a small drift surface.
- *Reuse the exact final `oss-fuzz/Dockerfile` for exploration*: can't — that file doesn't
  exist yet until after generation; this decision produces the probe image early and lets
  final generation reuse the same package list.

## 3. Detecting Docker/network unavailability before attempting the oss-fuzz environment

**Decision**: Before running any oss-fuzz-environment stage, run `docker info` (short
timeout, e.g. 10s). A nonzero exit code or timeout raises `EnvironmentUnavailableError`
with the captured stderr, which the CLI reports as an actionable failure and does **not**
route to agent fallback (FR-012). A subsequent probe-image `docker build` failure caused by
network/pull errors (e.g. can't reach `gcr.io`) is classified the same way by pattern-
matching common Docker pull-failure phrases (`"Error response from daemon"`, `"no such
host"`, `"i/o timeout"`) in the build's stderr; anything else is a genuine build/stage
failure eligible for agent fallback.

**Rationale**: FR-012 requires distinguishing "the environment itself isn't available" from
"the build is broken" — the former shouldn't burn an agent invocation. `docker info` is the
standard cheap daemon-reachability check (same idiom as `docker version --format`); it
fails fast and clearly when Docker isn't installed or the daemon isn't running, without
needing to attempt a real build first.

**Alternatives considered**: Only detect unavailability by letting the first real `docker
build`/`docker run` fail and inspecting its error text. Rejected as sole mechanism — no
cheap upfront check means every oss-fuzz run pays for a full probe-image build attempt just
to discover Docker isn't installed, and free-text error sniffing on a heavier command is a
noisier signal than a dedicated `docker info` preflight.

## 4. Environment executor interface shape

**Decision**: A small `EnvironmentExecutor` protocol in
`library_builder/environments/base.py` with two methods mirroring the two pipeline stages:

```python
class EnvironmentExecutor(Protocol):
    def run_library_build(self, analysis: AnalysisResult, workdir: Path, *, timeout: int) -> BuildExplorationResult: ...
    def run_harness_compile(self, install_dir: Path, workdir: Path, language: Language, *, extra_include_paths: list[str], extra_library_paths: list[str]) -> HarnessExplorationResult: ...
```

`LocalExecutor` wraps today's `exploration.explore`/`harness_explorer.explore_harness_compilation`
logic (host `subprocess`, unchanged behavior). `OssFuzzExecutor` builds/reuses the probe
image (Research #2) and runs each stage via the bind-mount `docker run` pattern (Research
#1), reusing the *same* linker-error-parsing/retry loop in `harness_explorer.py` (it's pure
text processing over stderr, agnostic to where the command ran) and the same
`build_library_script(..., oss_fuzz=True)` / `build_harness_script(..., oss_fuzz=True)`
template variants already used for final generation — so the exact script text validated
during exploration is the same text later copied into the generated OSS-Fuzz project,
closing today's "validated on host, pasted into a container project" gap.

**Rationale**: Keeping the retry/parsing logic in `harness_explorer.py` untouched and only
swapping *how a command runs* (host subprocess vs. `docker run`) is the minimal change that
satisfies FR-003/FR-004 without duplicating the transitive-dependency-discovery logic per
environment. `BuildExplorationResult`/`HarnessExplorationResult` already carry everything
the rest of the pipeline (generation, stats, agent prompts) needs, so no new result type is
introduced — only an `environment: Environment` field (see data-model.md).

**Alternatives considered**: A single `explore(analysis, workdir, environment=...)` function
with an `if environment == OSS_FUZZ` branch inline. Rejected: mixes host-subprocess and
Docker command construction in one function, harder to test in isolation (Constitution
Principle IV wants the Docker boundary mockable independent of local-environment tests) and
harder to keep each executor under the complexity-8 limit (Principle I).

## 5. Fixing `agents/scripts/check_local_build.sh` and `check_docker_build.sh` (FR-010)

**Current bugs found**:
- `check_local_build.sh` references `$target_dir`, which is never assigned (only
  `work_dir` is), and calls `./build_lib.sh && ./build_harness.sh && ./default_harness` —
  none of those filenames exist; the real generated scripts are `build_library.sh` and
  `compile_harnesses.sh`, and the compiled binary lands under `out/`, not
  `./default_harness`.
- `check_docker_build.sh` never `cd`s into `work_dir` before `docker build .`, and passes
  `"compile && timeout 2s /out/default_harness"` as a single positional argument to `docker
  run` — the image's default entrypoint is not a shell, so that whole string is
  misinterpreted as an argv rather than executed as a shell command. There is no
  `default_harness` target in the generated project either.

**Decision**: Rewrite both scripts to the proven pattern from
`tests/run_ground_truth.py::_docker_build_and_compile` and the actual local generation
layout:
- `check_local_build.sh <work_dir>`: `cd`s into `work_dir`, runs `bash build_library.sh &&
  bash compile_harnesses.sh`, then checks `install/lib/*.a`, `install/include/`, and that
  `out/` is non-empty — exiting non-zero with a clear message on any missing artifact.
- `check_docker_build.sh <oss_fuzz_project_dir> <project_name> [harness_name]`: `cd`s into
  `oss_fuzz_project_dir`, runs `docker build -t <project_name>:harnessbuddy-check .`, then
  `docker run --rm --entrypoint bash <tag> -c "compile && test -n \"\$(ls -A /out)\""`
  (or, when `harness_name` is given, checks that specific `/out/<harness_name>` exists and
  is executable) — matching the exact invocation shape already proven to work.

**Rationale**: These are the scripts FR-009 requires the repair agent to invoke as its
verification step; they must actually run correctly against what HarnessBuddy generates
today, not the naming scheme from an earlier iteration of the project.

**Alternatives considered**: Point agents at `tests/run_ground_truth.py` directly instead of
fixing `agents/scripts/*.sh`. Rejected: that script is a multi-library dev/CI harness with
its own `LIBS` list and ground-truth-harness lookup, not a single-project, agent-invokable
check; `agents/scripts/` is explicitly the location the spec and the user's request point
at (FR-010).

## 6. Threading environment choice through the LLM repair agent's prompt

**Decision**: `build_library_prompt`/`build_harness_prompt` (in `agents.py`) accept the
selected `Environment` and append one line telling the agent exactly which command proves
its fix: `bash agents/scripts/check_local_build.sh <workdir>` for local, or `bash
agents/scripts/check_docker_build.sh <oss_fuzz_project_dir> <project_name>` for oss-fuzz.
`agents/library_builder/SKILL.md` and `agents/harness_builder/SKILL.md` step-by-step
instructions are updated to say "run the verification command given in the failure
context" instead of "run build_library.sh yourself" / "run build_harness.sh yourself",
since those exact filenames/invocations no longer match the per-environment path.

**Rationale**: FR-009 requires the agent to verify its fix in the selected environment via
`agents/scripts/`; today the SKILL.md files never mention those scripts at all — the agent
independently re-runs the build script itself, which only ever proves the fix on the host
regardless of which environment the user selected. Passing the concrete command in the
prompt (rather than making the agent infer it) keeps the prompt built from typed context
per Constitution Principle VI, not ad hoc string interpolation.

**Alternatives considered**: Have the agent infer which script to run from the SKILL.md's
static text alone. Rejected: SKILL.md is environment-agnostic instruction text shared across
runs; the concrete workdir/project-name/tag arguments those scripts need only exist per-run
and belong in the appended context block, not the static instructions.

## 7. CLI flag and `--skip-validation` interaction

**Decision**: Add `--environment {local,oss-fuzz}` to `generate`, default `local`. When
`--skip-validation` is passed, the per-stage environment gate is skipped entirely (both
stages still run to produce the artifacts generation needs, but their pass/fail result does
not block progressing to generation) — preserving `--skip-validation`'s existing meaning of
"don't let validation stop me" rather than introducing a second flag for the same concept.

**Rationale**: Matches the spec's Assumptions section, which flags the exact
`--skip-validation` interaction as a planning-level detail. Reusing the existing flag avoids
adding a redundant CLI surface (Constitution Principle V).

**Alternatives considered**: A separate `--no-environment-gate` flag. Rejected: two flags
that both mean "don't let a failed check stop the pipeline" is confusing surface area for
no behavioral gain.
