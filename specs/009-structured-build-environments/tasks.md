---

description: "Task list for Structured Build Environments"
---

# Tasks: Structured Build Environments

**Input**: Design documents from `/specs/009-structured-build-environments/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md (all present)

**Tests**: Included — this project's constitution (Principle IV) makes test coverage for
new capabilities, malformed input, and boundary conditions non-negotiable, not optional.

**Organization**: Tasks are grouped by user story to enable independent implementation and
testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Every task includes exact file paths

## Path Conventions

Single project layout (existing): `src/harnessbuddy/`, `tests/`, `agents/` at repository root.

---

## Phase 1: Setup

**Purpose**: New package scaffolding and test infrastructure this feature needs

- [ ] T001 Create `src/harnessbuddy/library_builder/environments/__init__.py` (empty package init)
- [ ] T002 [P] Register a `docker` pytest marker in `pyproject.toml` `[tool.pytest.ini_options]`
  (mirroring the existing `agentic`/`build_matrix` markers) and append `and not docker` to
  `addopts` so Docker-dependent tests are skipped by default

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core abstraction and model changes that both P1 user stories build on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 Define the `Environment` enum (`LOCAL`, `OSS_FUZZ`), the `EnvironmentExecutor`
  protocol (`run_library_build`, `run_harness_compile`, `check_availability`), and the
  `EnvironmentUnavailableError` exception in
  `src/harnessbuddy/library_builder/environments/base.py`
- [ ] T004 Add an `environment: Environment` field (defaulting to `Environment.LOCAL`) to
  `BuildExplorationResult` and `HarnessExplorationResult` in
  `src/harnessbuddy/library_builder/models.py` (depends on T003)
- [ ] T005 Add an `environment: str` field to `RunStats` (`to_dict()` included) and thread it
  through the `_write_run_stats` call sites in `src/harnessbuddy/library_builder/stats.py`
  and `src/harnessbuddy/cli.py` (depends on T004)
- [ ] T006 Add `--environment {local,oss-fuzz}` (default `local`) to the `generate` subparser
  in `src/harnessbuddy/cli.py` — parsing only, no dispatch behavior yet

**Checkpoint**: Foundation ready — User Story 1 and User Story 2 can now proceed

---

## Phase 3: User Story 1 - Choose a target environment for a run (Priority: P1) 🎯 MVP (part 1/2)

**Goal**: `--environment` selects an executor; the local path is refactored behind
`LocalExecutor` with byte-identical behavior to today; the final report and `stats.json`
state which environment was used.

**Independent Test**: Run `generate` with no `--environment` flag and with
`--environment local` against the same fixture repository; both produce output identical to
pre-feature behavior, and both report `environment: local`. (Selecting `oss-fuzz` is
accepted by the parser here but is only functionally validated once User Story 2 lands —
see Dependencies below.)

### Tests for User Story 1

> Write these first; T007-T009 should fail until the matching implementation task lands.

- [ ] T007 [P] [US1] Test: `--environment` accepts `local`/`oss-fuzz`, defaults to `local`,
  and rejects invalid values, in `tests/test_cli.py`
- [ ] T008 [P] [US1] Test: default (no flag) and explicit `--environment local` runs produce
  identical `local/`/`oss-fuzz/` output and `stats.json` content to pre-feature behavior, in
  `tests/test_cli.py`
- [ ] T009 [P] [US1] Test: `LocalExecutor.run_library_build`/`run_harness_compile` return
  results tagged `environment=Environment.LOCAL` with behavior identical to calling
  `exploration.explore`/`harness_explorer.explore_harness_compilation` directly, in
  `tests/library_builder/environments/test_local.py`

### Implementation for User Story 1

- [ ] T010 [US1] Implement `LocalExecutor` (`run_library_build`, `run_harness_compile`,
  `check_availability` as a no-op) in
  `src/harnessbuddy/library_builder/environments/local.py`, delegating to
  `exploration.explore`/`harness_explorer.explore_harness_compilation` and tagging the
  returned result with `environment=Environment.LOCAL` via `dataclasses.replace` (depends on
  T003, T004)
- [ ] T011 [US1] Wire `--environment` to executor selection in `_cmd_generate`
  (`Environment.LOCAL` → `LocalExecutor()`, `Environment.OSS_FUZZ` → placeholder until US2)
  in `src/harnessbuddy/cli.py` (depends on T006, T010)
- [ ] T012 [US1] Replace the direct `build_library`/`build_harness` module-level calls in
  `_run_library_phase`/`_run_harness_phase` with `executor.run_library_build`/
  `executor.run_harness_compile` in `src/harnessbuddy/cli.py` (depends on T011)
- [ ] T013 [US1] Add the selected environment to the final success/failure print output and
  to `stats.json` in `src/harnessbuddy/cli.py` (depends on T005, T012)

**Checkpoint**: `--environment local` (and the default) is fully functional and reports
correctly. `--environment oss-fuzz` is accepted but not yet functionally validated.

---

## Phase 4: User Story 2 - Validate each stage as it happens, in the target environment (Priority: P1) 🎯 MVP (part 2/2)

**Goal**: `OssFuzzExecutor` builds and validates each stage inside the real OSS-Fuzz
base-builder container, per stage, before generation; Docker/network unavailability is
reported distinctly from a build failure and never triggers agent fallback; final generation
copies only the script text actually validated for the matching output directory's
environment.

**Independent Test**: Run `generate --environment oss-fuzz` against a fixture repository
with Docker available; confirm the library-build stage is validated in-container before the
harness-compile stage starts, the final report states `environment: oss-fuzz`, and stopping
Docker beforehand produces an actionable failure with no agent invocation.

### Tests for User Story 2

> Write these first; T014-T017 should fail until the matching implementation task lands.

- [ ] T014 [P] [US2] Test: `check_availability` raises `EnvironmentUnavailableError` when
  `docker info` fails (mocked subprocess), in
  `tests/library_builder/environments/test_oss_fuzz.py`
- [ ] T015 [P] [US2] Test (`@pytest.mark.docker`): `OssFuzzExecutor.run_library_build`/
  `run_harness_compile` against a real fixture library succeed end-to-end inside the
  container and return `environment=Environment.OSS_FUZZ`, in
  `tests/library_builder/environments/test_oss_fuzz.py`
- [ ] T016 [P] [US2] Test: a stage failure in the selected environment stops the pipeline
  before the next stage runs and before generation, for both environments, in
  `tests/test_cli.py`
- [ ] T017 [P] [US2] Test: `generate_local`/`generate_oss_fuzz` fall back to the regenerated
  template (never a mismatched-environment script copy) when the validated result's
  `environment` doesn't match the output directory's environment, in
  `tests/library_builder/local/test_generation.py` and
  `tests/library_builder/oss_fuzz/test_generation.py`

### Implementation for User Story 2

- [ ] T018 [US2] Add an `environment: Environment` parameter to `exploration.explore()`
  selecting `host_fallbacks=(environment is Environment.LOCAL)` and routing script execution
  through a caller-supplied run primitive instead of calling `run_command_streaming`
  directly, in `src/harnessbuddy/library_builder/exploration.py` (depends on T003)
- [ ] T019 [US2] Add the same `environment` parameter to
  `harness_explorer.explore_harness_compilation()`, selecting
  `oss_fuzz=(environment is Environment.OSS_FUZZ)` when generating each probe attempt's
  script and routing execution through the same run primitive, in
  `src/harnessbuddy/library_builder/harness_explorer.py` (depends on T003)
- [ ] T020 [US2] Implement `OssFuzzExecutor.check_availability()` running `docker info` with
  a short timeout, raising `EnvironmentUnavailableError` on nonzero exit/timeout, in
  `src/harnessbuddy/library_builder/environments/oss_fuzz.py` (depends on T003)
- [ ] T021 [US2] Implement probe-image build/rebuild
  (`harnessbuddy-probe/<project_name>:latest` from `gcr.io/oss-fuzz-base/base-builder`,
  installing `state.apt_packages`, cloning the repo at `repo_ref`), rebuilding only when the
  apt-package set has changed since the last build, in
  `src/harnessbuddy/library_builder/environments/oss_fuzz.py` (depends on T020)
- [ ] T022 [US2] Implement `OssFuzzExecutor.run_library_build`: calls
  `exploration.explore(..., environment=Environment.OSS_FUZZ)` with a run primitive that
  executes `docker run --rm --entrypoint bash -v <workdir>:<container_workdir> -w
  <container_workdir> <probe_image> -c "bash build_library.sh"`, in
  `src/harnessbuddy/library_builder/environments/oss_fuzz.py` (depends on T018, T021)
- [ ] T023 [US2] Implement `OssFuzzExecutor.run_harness_compile` the same way for
  `harness_explorer.explore_harness_compilation`, in
  `src/harnessbuddy/library_builder/environments/oss_fuzz.py` (depends on T019, T021)
- [ ] T024 [US2] Classify probe-image build/run failures as `EnvironmentUnavailableError`
  when stderr matches known Docker pull/network-failure phrases (`"Error response from
  daemon"`, `"no such host"`, `"i/o timeout"`), else as a normal stage failure, in
  `src/harnessbuddy/library_builder/environments/oss_fuzz.py` (depends on T021)
- [ ] T025 [US2] Catch `EnvironmentUnavailableError` in `_cmd_generate` ahead of the existing
  `BuildFailureError`/`LLMBudgetError` handling, printing an actionable message and returning
  1 without invoking agent fallback; replace the US1 oss-fuzz placeholder from T011 with the
  real `OssFuzzExecutor()`, in `src/harnessbuddy/cli.py` (depends on T011, T020)
- [ ] T026 [US2] Guard the `script_path`-verbatim-copy shortcut so `generate_local` only
  reuses a result whose `environment == Environment.LOCAL` and `generate_oss_fuzz` only
  reuses one whose `environment == Environment.OSS_FUZZ`, falling back to the regenerated
  template otherwise, in `src/harnessbuddy/library_builder/local/generation.py` and
  `src/harnessbuddy/library_builder/oss_fuzz/generation.py` (depends on T004)

**Checkpoint**: Both environments are fully functional and gated per-stage (FR-001 through
FR-008, FR-012 satisfied).

---

## Phase 5: User Story 3 - Agent repair verifies fixes in the target environment (Priority: P2)

**Goal**: The repair agent's prompt tells it the correct verification command for the
selected environment; `agents/scripts/check_local_build.sh` and `check_docker_build.sh` are
fixed to match the real generated script names/arguments.

**Independent Test**: Force a build failure, invoke agent fallback with `--environment
oss-fuzz`, and confirm the agent's prompt/transcript references `check_docker_build.sh` with
the correct project directory and name; run both scripts by hand against a known-good and a
known-broken project and confirm their exit codes and messages.

### Tests for User Story 3

> Write these first; T027-T029 should fail until the matching implementation task lands.

- [ ] T027 [P] [US3] Test: `build_library_prompt`/`build_harness_prompt` include the correct
  environment-specific verification command for both `Environment.LOCAL` and
  `Environment.OSS_FUZZ`, in `tests/library_builder/test_agents.py`
- [ ] T028 [P] [US3] Test: `check_local_build.sh` exits 0 against a known-good generated
  project and non-zero with a clear message against a known-broken one, invoked via
  subprocess, in `tests/agents/test_check_scripts.py`
- [ ] T029 [P] [US3] Test (`@pytest.mark.docker`): `check_docker_build.sh` exits 0 against a
  known-good generated oss-fuzz project and non-zero against a known-broken one, in
  `tests/agents/test_check_scripts.py`

### Implementation for User Story 3

- [ ] T030 [P] [US3] Rewrite `agents/scripts/check_local_build.sh` to accept `<work_dir>`,
  run `bash build_library.sh && bash compile_harnesses.sh`, and check `install/lib/*.a`,
  `install/include/`, and `out/`, per `contracts/agent-scripts.md`
- [ ] T031 [P] [US3] Rewrite `agents/scripts/check_docker_build.sh` to accept
  `<oss_fuzz_project_dir> <project_name> [harness_name]` and use the proven `docker build`
  + `docker run --rm --entrypoint bash ... -c "compile && ..."` invocation shape from
  `tests/run_ground_truth.py`, per `contracts/agent-scripts.md`
- [ ] T032 [US3] Add an `environment: Environment` parameter to `build_library_prompt`/
  `build_harness_prompt` in `src/harnessbuddy/library_builder/agents.py`, appending the
  concrete verification command to the generated prompt (depends on T030, T031)
- [ ] T033 [US3] Thread the selected `Environment` from `_run_library_phase`/
  `_run_harness_phase` into `invoke_library_builder_agent`/`invoke_harness_builder_agent` in
  `src/harnessbuddy/cli.py` and `src/harnessbuddy/library_builder/agents.py` (depends on
  T032)
- [ ] T034 [P] [US3] Update `agents/library_builder/SKILL.md` step 7 and
  `agents/harness_builder/SKILL.md` step 6 to say "run the verification command given in the
  failure context below" instead of manually re-invoking the build/harness script

**Checkpoint**: FR-009 and FR-010 satisfied; SC-004 verifiable end-to-end.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T035 [P] Run `uv run ruff format`, `uv run ruff check`, `uv run ty check` across all
  touched files and fix every warning
- [ ] T036 [P] Run `uv run pytest -q` (default marker set) and confirm zero regressions; run
  `uv run pytest -q -m docker` separately on a host with Docker available
- [ ] T037 Walk through `quickstart.md` scenarios 1-7, recording which were verified vs.
  skipped (e.g. no Docker available), matching this project's existing per-feature
  verification note convention (see `plans/oss-fuzz-builder/progress.md`)
- [ ] T038 [P] Cross-check `spec.md`'s SC-001 through SC-005 against the tests/scenarios
  above and note any gap before closing out the feature

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS both User Story 1 and User Story 2
- **User Story 1 (Phase 3)**: Depends on Foundational
- **User Story 2 (Phase 4)**: Depends on Foundational; does not depend on User Story 1's
  tasks being *complete*, but T025 replaces the placeholder T011 leaves for
  `Environment.OSS_FUZZ`, so land Phase 3 before finishing T025 in practice
- **User Story 3 (Phase 5)**: Depends on Foundational and on both executors existing (T010,
  T022, T023) since its verification commands are environment-specific
- **Polish (Phase 6)**: Depends on all three user stories being complete

### Important note on User Story 1 vs. User Story 2

Both are labeled P1 in the spec and are functionally coupled: User Story 1's own acceptance
scenario for the oss-fuzz choice ("library build and harness compilation are executed and
validated inside an OSS-Fuzz-equivalent container") is only true once User Story 2's
`OssFuzzExecutor` exists. Treat Phase 3 + Phase 4 together as the MVP — do not present
`--environment oss-fuzz` as complete to users after Phase 3 alone.

### Within Each User Story

- Tests are written first and confirmed failing before the corresponding implementation
  task; confirm they pass once implementation lands
- Foundational types before executors; executors before CLI wiring; CLI wiring before
  reporting

### Parallel Opportunities

- T007, T008, T009 (US1 tests) can be written in parallel — different files/fixtures, no
  cross-dependency
- T014, T015, T016, T017 (US2 tests) can be written in parallel once T003/T004 exist, even
  before their corresponding implementation tasks land
- T030 and T031 (the two shell script rewrites) are fully independent files
- T035, T036, T038 (Polish) can run in parallel

---

## Parallel Example: User Story 2

```bash
# Once Foundational (T003-T006) is complete, these can be drafted in parallel:
Task: "Test: check_availability raises EnvironmentUnavailableError when docker info fails, in tests/library_builder/environments/test_oss_fuzz.py"
Task: "Test: a stage failure in the selected environment stops the pipeline before the next stage, in tests/test_cli.py"
Task: "Test: generate_local/generate_oss_fuzz fall back to the regenerated template on environment mismatch, in tests/library_builder/local/test_generation.py and tests/library_builder/oss_fuzz/test_generation.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 + User Story 2 together)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks both P1 stories)
3. Complete Phase 3: User Story 1 (local path fully refactored, reporting in place)
4. Complete Phase 4: User Story 2 (oss-fuzz path fully functional)
5. **STOP and VALIDATE**: run quickstart.md scenarios 1-5 end-to-end
6. This is the MVP — `--environment {local,oss-fuzz}` both work and validate per-stage

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. User Story 1 → local path refactored, no user-visible regression (safe to ship alone
   only if `--environment oss-fuzz` is hidden/rejected until Phase 4 lands)
3. User Story 2 → oss-fuzz path functional → MVP complete → demo/deploy
4. User Story 3 → agent repair catches up to environment-aware verification → demo/deploy

### Parallel Team Strategy

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (local refactor + CLI wiring + reporting)
   - Developer B: User Story 2 (Docker executor — the larger of the two stories)
   - Developer C: starts User Story 3's script rewrites (T030, T031) immediately — they have
     no dependency on the executors, only T032/T033 do
3. User Story 3's remaining tasks (T032, T033) wait for at least one real executor to exist
   for a meaningful prompt-content test

---

## Notes

- [P] tasks touch different files with no unresolved dependency
- [Story] label maps each task to its user story for traceability
- Commit after each task or logical group
- Stop at either Phase 3 or Phase 4 checkpoint to validate independently, but present the
  feature as complete only once both land (see the MVP note above)
- Docker-gated tests (`@pytest.mark.docker`) are skipped by default per this project's
  constitution (Principle IV) and this feature's `pyproject.toml` marker registration (T002)
