---

description: "Task list for feature implementation"
---

# Tasks: Unified Build Verification

**Input**: Design documents from `/specs/011-unify-build-verification/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/verification-scripts.md,
contracts/workspace-layout.md, quickstart.md

**Tests**: Included — constitution Principle IV requires behavior coverage for new capabilities,
and this feature's whole point is that HarnessBuddy's own pipeline and the repair agent produce
provably identical verification behavior, which only tests (not manual inspection) can guarantee.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing
of each story. Task tracking for this feature lives in beads (`bd`), not this file — see the beads
issue IDs noted per task below. This file remains the source of task content for `/speckit-implement`.

**Beads epic**: `harnessbuddy-jsn` — `bd show harnessbuddy-jsn` for the full tree
(Setup=`harnessbuddy-jsn.1`, Foundational=`harnessbuddy-jsn.2`, US1=`harnessbuddy-jsn.3`,
US2=`harnessbuddy-jsn.4`, US3=`harnessbuddy-jsn.5`, Polish=`harnessbuddy-jsn.6`). Each task below
maps 1:1 to a child issue: T001=`harnessbuddy-jsn.1.1`, T002=`harnessbuddy-jsn.2.1`,
T003=`harnessbuddy-jsn.2.2`, T004=`harnessbuddy-jsn.2.3`, T005=`harnessbuddy-jsn.2.4`,
T006=`harnessbuddy-jsn.3.1`, T007=`harnessbuddy-jsn.3.2`, T008=`harnessbuddy-jsn.3.3`,
T009=`harnessbuddy-jsn.3.4`, T010=`harnessbuddy-jsn.3.5`, T011=`harnessbuddy-jsn.3.6`,
T012=`harnessbuddy-jsn.4.1`, T013=`harnessbuddy-jsn.4.2`, T014=`harnessbuddy-jsn.4.3`,
T015=`harnessbuddy-jsn.4.4`, T016=`harnessbuddy-jsn.4.5`, T017=`harnessbuddy-jsn.4.6`,
T018=`harnessbuddy-jsn.4.7`, T019=`harnessbuddy-jsn.4.8`, T020=`harnessbuddy-jsn.4.9`,
T021=`harnessbuddy-jsn.4.10`, T022=`harnessbuddy-jsn.5.1`, T023=`harnessbuddy-jsn.5.2`,
T024=`harnessbuddy-jsn.5.3`, T025=`harnessbuddy-jsn.6.1`, T026=`harnessbuddy-jsn.6.2`,
T027=`harnessbuddy-jsn.6.3`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

Single project: `src/harnessbuddy/`, `tests/`, `agents/scripts/` at repository root (matches
plan.md's Project Structure).

---

## Phase 1: Setup

**Purpose**: No new external dependency (research.md Technical Context) — the one prep task is
keeping project documentation accurate about the scripts' new dual role.

- [X] T001 [P] Update `CLAUDE.md`'s Source Map/Development Standards to note that
  `agents/scripts/check_local_build.sh`/`check_docker_build.sh` are invoked both by the repair agent
  and directly by `LocalExecutor`/`OssFuzzExecutor` (no longer agent-only helper scripts)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The two new shared modules every story's work reads or writes: the verification-script
wrapper and the extracted workspace-materialization writers

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T002 [P] Create `VerificationResult` dataclass and `run_docker_verification(workspace, project_name)`
  / `run_local_verification(workspace)` wrapper functions (subprocess calls to
  `agents/scripts/check_docker_build.sh` / `check_local_build.sh`, per data-model.md) in new
  `src/harnessbuddy/library_builder/environments/verification.py`
- [X] T003 [P] Create `write_project_yaml(workspace, analysis)` / `write_dockerfile(workspace, analysis,
  *, include_bear: bool)` / `write_build_sh(workspace)` functions in new
  `src/harnessbuddy/library_builder/oss_fuzz/workspace.py`, moving the existing content of
  `oss_fuzz/generation.py`'s `_write_project_yaml`/`_write_dockerfile`/`_write_build_sh` verbatim
  and generalizing `_write_dockerfile`'s always-add-`bear` behavior into the `include_bear` parameter
- [X] T004 [P] Unit tests for `verification.py`'s wrappers — correct argv construction, `VerificationResult`
  field population from a mocked subprocess, and that `_is_environment_unavailable`-style stderr
  pattern matching still works against `VerificationResult.stderr` (depends on T002) in new
  `tests/library_builder/environments/test_verification.py`
- [X] T005 [P] Unit tests for `workspace.py`'s writers — output is byte-identical to today's
  `_write_project_yaml`/`_write_dockerfile`/`_write_build_sh`, and `include_bear=False` omits exactly
  the `bear` package (depends on T003) in new `tests/library_builder/oss_fuzz/test_workspace.py`

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 - One verification command per environment, used by everyone (Priority: P1)

**Goal**: For each environment, HarnessBuddy's own pipeline and the repair agent invoke the
identical verification script — no separate ad hoc pipeline-side check.

**Independent Test**: For the **local** environment (fully self-contained, no dependency on US2's
oss-fuzz workspace work): confirm `LocalExecutor.run_library_build`/`run_harness_compile` and the
repair agent's own verification step both shell out to `agents/scripts/check_local_build.sh`. Full
oss-fuzz-environment delivery of this story completes together with US2 (Phase 4) — see Dependencies.

### Implementation for User Story 1

- [X] T006 [US1] In `LocalExecutor.run_library_build` (`src/harnessbuddy/library_builder/environments/local.py`),
  after `exploration.explore` writes `build_library.sh`, write a no-op stub `compile_harnesses.sh`
  (reusing `local/generation.py`'s `_COMPILE_HARNESSES_SH_STUB` text) if one doesn't already exist,
  then call `verification.run_local_verification(workdir)` and populate the returned
  `BuildExplorationResult` from that `VerificationResult` instead of trusting `explore()`'s raw
  subprocess result directly (depends on T002)
- [X] T007 [US1] In `LocalExecutor.run_harness_compile` (same file), after
  `harness_explorer.explore_harness_compilation` converges on real `compile_harnesses.sh` content,
  call `verification.run_local_verification(workdir)` again and populate the returned
  `HarnessExplorationResult` from that `VerificationResult` (depends on T002, T006)
- [X] T008 [P] [US1] Remove the `oss_fuzz_project_dir` parameter from `_verification_command`,
  `build_library_prompt`, and `build_harness_prompt` in `src/harnessbuddy/library_builder/agents.py`;
  the oss-fuzz branch uses `workdir` directly, matching every other branch
- [X] T009 [US1] Update `build_library`, `build_harness`, `_run_library_phase`, `_run_harness_phase` in
  `src/harnessbuddy/cli.py` to stop threading `oss_output_path` into agent invocations, since
  `agents.py` no longer accepts it (depends on T008)
- [X] T010 [US1] Record the literal verification command (`VerificationResult.command`) in the final
  console report and add it to `stats.json` via `RunStats` (FR-010), in `src/harnessbuddy/cli.py` and
  `src/harnessbuddy/library_builder/stats.py` (depends on T002, T006, T009)
- [X] T011 [US1] Extend `tests/library_builder/environments/test_local.py`: `run_library_build`/
  `run_harness_compile` invoke `check_local_build.sh` via `run_local_verification` (mocked subprocess
  boundary), and the stub `compile_harnesses.sh` exists before the library-only check runs (depends
  on T006, T007)

**Checkpoint**: At this point, User Story 1 is fully functional and testable independently for the
local environment — its oss-fuzz-environment half lands with US2 below.

---

## Phase 4: User Story 2 - The real oss-fuzz project layout exists throughout the run (Priority: P1)

**Goal**: The `.harnessbuddy/<project>/` workspace becomes the real, buildable OSS-Fuzz project
directory as soon as its pieces are known, and final generation copies those already-validated
files instead of re-deriving them. This also completes US1's oss-fuzz-environment half, since
`check_docker_build.sh` (US1's shared script) needs a real `Dockerfile`/`build.sh` to run against.

**Independent Test**: Inspect the workspace mid-run (or with `--keep-workdir` after) and confirm
`Dockerfile`/`build.sh`/`project.yaml`/`harness_source/` exist alongside `build_library.sh`/
`compile_harnesses.sh`; diff the final `oss-fuzz/` output against the workspace and confirm they
match (contracts/workspace-layout.md).

### Implementation for User Story 2

- [X] T012 [US2] In `OssFuzzExecutor` (`src/harnessbuddy/library_builder/environments/oss_fuzz.py`),
  replace the tempdir-based synthetic probe Dockerfile with real workspace materialization: call
  `workspace.write_project_yaml`, `workspace.write_dockerfile(workspace, analysis, include_bear=True)`,
  `workspace.write_build_sh(workspace)`, create `harness_source/`, and write a stub
  `compile_harnesses.sh` (`oss_fuzz/generation.py`'s `_COMPILE_HARNESSES_SH_STUB`) if none exists yet,
  then `docker build` that real `Dockerfile` as the run-scoped image (depends on T003)
- [X] T013 [US2] Remove `_ProbeImageBuildError` and the tempdir-based Dockerfile construction from
  `environments/oss_fuzz.py`, now dead after T012 (depends on T012)
- [X] T014 [US2] Update `OssFuzzExecutor.run_library_build` to call
  `verification.run_docker_verification(workspace, project_name)` as its pass/fail gate — replacing
  the per-stage `explore(..., run=_docker_run_factory(...))` docker-run approach — while `explore()`
  still writes `build_library.sh` into the workspace first (depends on T002, T012)
- [X] T015 [US2] Update `OssFuzzExecutor.run_harness_compile` so `harness_explorer.explore_harness_compilation`'s
  retry loop keeps using a fast direct `docker run --entrypoint bash <image> -c "bash
  compile_harnesses.sh"` against the already-built image for its internal attempts (research.md #2 —
  no `docker build` per attempt), then calls `verification.run_docker_verification(workspace,
  project_name)` once more, after discovery converges or exhausts its attempts, for the stage's
  actual pass/fail result (depends on T002, T012, T014)
- [X] T016 [US2] Update `generate_oss_fuzz` in `src/harnessbuddy/library_builder/oss_fuzz/generation.py`
  to copy `project.yaml`, `build.sh`, `build_library.sh`, `compile_harnesses.sh`, and
  `harness_source/*` (excluding the discovery-only probe source) from the workspace instead of
  re-deriving them, calling `workspace.write_dockerfile(output_path, analysis, include_bear=False)`
  for the one file that must differ from its workspace counterpart (depends on T003, T012)
- [X] T017 [P] [US2] Update `generate_local` in `src/harnessbuddy/library_builder/local/generation.py`
  the same way — copy `build_library.sh`/`compile_harnesses.sh`/`harness_src/*` from the workspace
  instead of re-deriving them; `setup.sh`'s own writer is unchanged (depends on T003)
- [X] T018 [US2] Simplify the remaining template-fallback branches in both generation modules'
  `_write_build_library_sh`/`_write_compile_harnesses_sh` (or their replacements) to the single
  "exploration never ran at all" case (depends on T016, T017)
- [X] T019 [P] [US2] Add tests to `tests/library_builder/environments/test_oss_fuzz.py`: the workspace
  contains `Dockerfile`/`build.sh`/`project.yaml`/`harness_source/` after `run_library_build`'s first
  call (mocked subprocess boundary) (depends on T012)
- [X] T020 [P] [US2] Add regression tests to `tests/library_builder/oss_fuzz/test_generation.py` and
  `tests/library_builder/local/test_generation.py`: generated output files are byte-identical to
  their workspace counterparts, except the bear-stripped `oss-fuzz/Dockerfile` (depends on T016, T017)
- [X] T021 [US2] Add a Docker-gated (`HARNESSBUDDY_RUN_DOCKER=1`) integration test in
  `tests/library_builder/environments/test_oss_fuzz.py`: a full `run_library_build` →
  `run_harness_compile` sequence against a real fixture repo leaves a workspace that
  `bash agents/scripts/check_docker_build.sh <workspace> <project>`, run independently afterward,
  also passes (depends on T014, T015, T019)

**Checkpoint**: User Stories 1 and 2 together deliver the full oss-fuzz-environment behavior: one
shared script, run against a real project layout that exists throughout the run.

---

## Phase 5: User Story 3 - Failure reports stay useful without separate stage gates (Priority: P2)

**Goal**: Even though verification is now one atomic check per environment, the combined output
still makes clear which stage (library build vs. harness compile) failed.

**Independent Test**: Break a library build and, separately, a harness-compile step in a fixture
project; confirm the combined output from the shared script makes clear which stage failed in each
case.

### Implementation for User Story 3

- [X] T022 [US3] Add a clear `echo "=== build_library.sh ==="` / `echo "=== compile_harnesses.sh
  ==="` marker before each invocation inside the generated `build.sh`, in
  `workspace.write_build_sh` (`src/harnessbuddy/library_builder/oss_fuzz/workspace.py`) (depends on
  T003)
- [X] T023 [US3] Add a test asserting a library-build-only failure's captured output identifies it
  happened before harness compilation, and a harness-only failure's output shows the library build
  succeeded first, in `tests/library_builder/environments/test_oss_fuzz.py` (depends on T022)
- [X] T024 [P] [US3] Apply the same stage-marker convention to `check_local_build.sh`'s
  `build_library.sh && compile_harnesses.sh` sequence in `agents/scripts/check_local_build.sh`

**Checkpoint**: All three user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final quality gate and end-to-end confirmation

- [X] T025 [P] Run `ruff format`, `ruff check`, and `ty check` across all touched files
- [X] T026 [P] Confirm `CLAUDE.md`'s Source Map reflects the new `oss_fuzz/workspace.py` and
  `environments/verification.py` modules (depends on T001, since both touch the same file)
- [X] T027 Walk through `quickstart.md` scenarios 1-7 against real fixture repositories to confirm
  end-to-end behavior (depends on T025 and all user stories complete)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational. Local-environment delivery is fully
  independent; oss-fuzz-environment delivery also depends on User Story 2 (T012's real Dockerfile
  must exist before `run_docker_verification` can succeed against the workspace)
- **User Story 2 (Phase 4)**: Depends on Foundational. Functionally intertwined with User Story 1
  for the oss-fuzz environment (see above) — implement together for that environment
- **User Story 3 (Phase 5)**: Depends on Foundational and on User Story 2 (T022 edits the `build.sh`
  `write_build_sh` introduces in T012)
- **Polish (Phase 6)**: Depends on User Stories 1, 2, and 3 all being complete

### Within Each User Story

- US1: T006 → T007 (same file, sequential). T008 is independent of T006/T007 (different file,
  parallel). T009 depends on T008. T010 depends on T002/T006/T009. T011 depends on T006/T007.
- US2: T012 → T013 → T014 → T015 (all sequential edits to `oss_fuzz.py`). T016 and T017 are
  independent of each other (different files, parallel) but both depend on T003/T012. T018 depends
  on both. T019 depends on T012. T020 depends on T016/T017. T021 depends on T014/T015/T019.
- US3: T022 → T023 (same file group, sequential). T024 is independent (different file, parallel).

### Parallel Opportunities

- T002 and T003 (Foundational) can run in parallel — different new files.
- T004 and T005 (Foundational tests) can run in parallel once their respective module lands.
- T008 (US1) can run in parallel with T006/T007 (US1) — `agents.py` vs. `environments/local.py`.
- T016 and T017 (US2) can run in parallel — `oss_fuzz/generation.py` vs. `local/generation.py`.
- T019 and T020 (US2 tests) can run in parallel once their dependencies land.
- T024 (US3) can run in parallel with T022 — `agents/scripts/check_local_build.sh` vs.
  `oss_fuzz/workspace.py`.
- T025 and T026 (Polish) can run in parallel.

---

## Parallel Example: Foundational Phase

```bash
# Launch both new shared modules together:
Task: "Create VerificationResult + wrappers in src/harnessbuddy/library_builder/environments/verification.py"
Task: "Create workspace writer functions in src/harnessbuddy/library_builder/oss_fuzz/workspace.py"

# Once each lands, launch its tests:
Task: "Unit tests for verification.py wrappers in tests/library_builder/environments/test_verification.py"
Task: "Unit tests for workspace.py writers in tests/library_builder/oss_fuzz/test_workspace.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2 together, local environment first)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3 (US1) for the local environment only (T006, T007, T011) — this alone proves the
   "shared script" mechanism end-to-end with the least risk (no Docker required)
4. **STOP and VALIDATE**: Run quickstart.md scenario 1
5. Complete the rest of Phase 3 (T008-T010) and all of Phase 4 (US2) — these together deliver the
   oss-fuzz environment's full story
6. **STOP and VALIDATE**: Run quickstart.md scenarios 2-6
7. Ship if ready — User Story 3 is a diagnostic-quality guarantee on top of the MVP, not additional
   user-facing capability

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. User Story 1 (local half) → validate independently → early proof of the core mechanism
3. User Story 1 (oss-fuzz half) + User Story 2 together → the feature's full stated value
4. User Story 3 → diagnostic quality preserved on top

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Task tracking for this feature is in beads, not a markdown checklist — see the completion report
  for issue IDs; check them off there (`bd close <id>`), not by editing this file's checkboxes.
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
