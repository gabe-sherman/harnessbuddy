---

description: "Task list for Library Feature Extraction for Fuzz Target Generation"
---

# Tasks: Library Feature Extraction for Fuzz Target Generation

**Input**: Design documents from `/specs/006-feature-extractor/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included as required tasks (not optional) — Constitution Principle IV mandates
behavior tests for every new capability in this repo.

**Tracking**: Every task below is also tracked as a `bd` issue (see mapping in each task and
the completion report) — this project uses `bd` for task tracking, not this file, per
`CLAUDE.md`. This file exists to satisfy the `/speckit-tasks` contract and as a durable,
reviewable plan; `bd ready` / `bd show <id>` are the source of truth for status.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Each task names its bd issue ID once created (see completion report)

## Path Conventions

Single project. Paths are relative to the repository root, matching `plan.md`'s Project
Structure: `src/harnessbuddy/feature_extractor/` (new tool package), `tests/feature_extractor/`
(new tests), plus small additions to `src/harnessbuddy/cli.py`, `tests/test_cli.py`, and
`pyproject.toml`.

---

## Phase 1: Setup

**Purpose**: Minimal project initialization — nothing here is user-story-specific.

- [ ] T001 Create the `feature_extractor` package skeleton: `src/harnessbuddy/feature_extractor/__init__.py` (empty package marker, no logic yet) (bd: harnessbuddy-fk1.1)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The JSON artifact's typed representation and the shared test fixture — both US1
(writes it) and US2 (reads it) depend on these; nothing story-specific belongs here.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T002 [P] Define `FeatureArtifactSet` and its nested dataclasses (`FunctionSignature`, `Param`, `Typedef`, `MacroDefinition`, `EnumDefinition`, `Enumerator`, `StructUnionDefinition`, `Field`) in `src/harnessbuddy/feature_extractor/models.py`, matching `contracts/feature-artifact.schema.json` and `data-model.md` (bd: harnessbuddy-fk1.2)
- [ ] T003 Implement `load_feature_artifact(path: Path) -> FeatureArtifactSet` in `src/harnessbuddy/feature_extractor/extraction.py` — parses JSON from disk, validates required fields/shape against `contracts/feature-artifact.schema.json`, raises a clear error on a `schema_version` mismatch or malformed file (depends on T002) (bd: harnessbuddy-fk1.3)
- [ ] T004 [P] Add `tests/feature_extractor/conftest.py`: a session-scoped fixture that resolves the repo-root `zlib_feature_test/` directory and skips every test in the package (with a clear reason) if that directory or its `compile_commands.json` is absent (research.md §6) (bd: harnessbuddy-fk1.4)

**Checkpoint**: Foundation ready — US1 and US2 implementation can now begin

---

## Phase 3: User Story 1 - Extract a library's API surface into a structured artifact file (Priority: P1) 🎯 MVP

**Goal**: `harnessbuddy extract-features <output-dir>` produces a JSON `FeatureArtifactSet`
(functions, typedefs, macros, enums, structs/unions) from an existing `compile_commands.json`.

**Independent Test**: Run `harnessbuddy extract-features zlib_feature_test` and confirm
`zlib_feature_test/features.json` contains zlib's real public API (`deflate`, `inflate`,
`z_stream`, `ZEXTERN`, etc.), matching `contracts/feature-artifact.schema.json`.

### Implementation for User Story 1

- [ ] T005 [P] [US1] Scaffold `src/harnessbuddy/feature_extractor/native/CMakeLists.txt` (C++17; `-Wall -Wextra -Werror -Wpedantic -Wshadow`; `find_package(Clang REQUIRED CONFIG)` / `find_package(LLVM REQUIRED CONFIG)`; links `clangTooling`, `clangASTMatchers`, `clangBasic`, `clangFrontend`) and `src/harnessbuddy/feature_extractor/native/.clang-format` (LLVM style) (bd: harnessbuddy-fk1.5)
- [ ] T006 [P] [US1] Implement `src/harnessbuddy/feature_extractor/native/src/json_writer.cpp` and `src/harnessbuddy/feature_extractor/native/include/feature_extractor.hpp` — a dependency-free JSON emitter producing the exact shape of `contracts/feature-artifact.schema.json` (depends on T005) (bd: harnessbuddy-fk1.6)
- [ ] T007 [P] [US1] Implement `src/harnessbuddy/feature_extractor/native/src/extraction_action.cpp` — a `RecursiveASTVisitor`-based `FrontendAction` extracting function (FR-004), typedef (FR-005), enum (FR-007), and struct/union (FR-008) declarations, tagging each with `is_public_api` via `clang::NamedDecl::getFormalLinkage()` plus a library-header-location check (research.md §5) and deduplicating declarations seen from more than one translation unit (spec edge case) (depends on T005) (bd: harnessbuddy-fk1.7)
- [ ] T008 [P] [US1] Implement `src/harnessbuddy/feature_extractor/native/src/macro_callbacks.cpp` — a `PPCallbacks` implementation registered on the `Preprocessor` extracting macro definitions (FR-006: name, function-like/object-like, params, value) (depends on T005) (bd: harnessbuddy-fk1.8)
- [ ] T009 [US1] Implement `src/harnessbuddy/feature_extractor/native/src/main.cpp` — builds a `clang::tooling::ClangTool` from the `compile_commands.json` path given as argv, runs the action/callbacks from T006-T008, writes the resulting JSON to the output path given as argv, skips stale/missing translation-unit entries with a warning instead of aborting (spec edge case), and produces a valid (possibly empty) artifact for a library with no public declarations (spec edge case) (depends on T006, T007, T008) (bd: harnessbuddy-fk1.9)
- [ ] T010 [P] [US1] Implement `src/harnessbuddy/feature_extractor/native_build.py` — builds `native/` once via `harnessbuddy.core.subprocesses.run_command_streaming`, caching the compiled binary under `.harnessbuddy/native-build/` keyed to the LLVM/Clang version CMake discovers, and surfaces build failures (e.g. missing LLVM/Clang dev packages) as a clear error rather than an opaque non-zero exit (research.md §2) (depends on T005) (bd: harnessbuddy-fk1.10)
- [ ] T011 [US1] Implement `extract_features(output_dir: Path) -> FeatureArtifactSet` in `src/harnessbuddy/feature_extractor/extraction.py` — resolves `<output_dir>/compile_commands.json` (raises a clear, actionable error naming the missing file and how to produce one if absent, per FR-003), builds/invokes the native binary via `native_build`, loads the result via `load_feature_artifact` (T003), and writes `<output_dir>/features.json`, overwriting any prior run (FR-010, FR-015) (depends on T003, T009, T010) (bd: harnessbuddy-fk1.11)
- [ ] T012 [US1] Register `harnessbuddy extract-features <output-dir>` in `src/harnessbuddy/cli.py`, wired to `extract_features`, with exit codes matching `contracts/cli-subcommands.md` (depends on T011) (bd: harnessbuddy-fk1.12)
- [ ] T013 [P] [US1] Integration test `tests/feature_extractor/test_extraction.py`: run `extract-features` against `zlib_feature_test/` and assert `deflate`/`inflate` appear as public functions with correct signatures, `z_stream`/`z_stream_s` appear (typedef + struct), and `ZEXTERN`/`ZEXPORT` appear as macros (depends on T012, T004) (bd: harnessbuddy-fk1.13)
- [ ] T014 [P] [US1] CLI test in `tests/test_cli.py`: `extract-features` against a directory with no `compile_commands.json` exits with code 1 and an actionable message (FR-003) (depends on T012) (bd: harnessbuddy-fk1.14)

**Checkpoint**: User Story 1 is fully functional and independently testable — this is the MVP.

---

## Phase 4: User Story 2 - Convert extracted artifacts into an oss-fuzz-gen-compatible benchmark file (Priority: P2)

**Goal**: `harnessbuddy generate-benchmark <output-dir>` converts an existing `features.json`
into a YAML file matching oss-fuzz-gen's benchmark input structure.

**Independent Test**: Given `zlib_feature_test/features.json` from US1, run
`harnessbuddy generate-benchmark zlib_feature_test` and confirm `zlib_feature_test/zlib.yaml`
matches `contracts/benchmark-yaml.schema.json` and loads via oss-fuzz-gen's
`Benchmark.from_yaml`.

### Implementation for User Story 2

- [ ] T015 [P] [US2] Add `pyyaml` to `[project.dependencies]` in `pyproject.toml`; run `uv lock` (bd: harnessbuddy-fk1.15)
- [ ] T016 [US2] Define `BenchmarkYaml` and `BenchmarkFunction` dataclasses in `src/harnessbuddy/feature_extractor/models.py` (depends on T002) (bd: harnessbuddy-fk1.16)
- [ ] T017 [US2] Implement `generate_benchmark(output_dir: Path, target_name: str | None, target_path: str | None) -> BenchmarkYaml` in `src/harnessbuddy/feature_extractor/benchmark_yaml.py` — loads `<output_dir>/features.json` via `load_feature_artifact` (T003), filters `functions` to `is_public_api == true` (FR-012), defaults `target_name` to `default_fuzzer` and `target_path` to `/src/harness_source/default_fuzzer.{ext}` unless overridden (FR-013/FR-014), serializes with PyYAML, and writes `<output_dir>/<project_name>.yaml`, overwriting any prior run (FR-015) (depends on T003, T015, T016) (bd: harnessbuddy-fk1.17)
- [ ] T018 [US2] Register `harnessbuddy generate-benchmark <output-dir> [--target-name] [--target-path]` in `src/harnessbuddy/cli.py` per `contracts/cli-subcommands.md` (depends on T017) (bd: harnessbuddy-fk1.18)
- [ ] T019 [P] [US2] Integration test `tests/feature_extractor/test_benchmark_yaml.py`: run `extract-features` then `generate-benchmark` against `zlib_feature_test/`, assert the YAML matches `contracts/benchmark-yaml.schema.json`, uses the correct defaults, and excludes zlib's internal/static helper functions present in the JSON (FR-012) (depends on T018, T004) (bd: harnessbuddy-fk1.19)
- [ ] T020 [P] [US2] CLI test in `tests/test_cli.py`: `generate-benchmark` against an output directory with no `features.json` exits with code 1 telling the user to run `extract-features` first (depends on T018) (bd: harnessbuddy-fk1.20)

**Checkpoint**: User Stories 1 and 2 both work independently.

---

## Phase 5: User Story 3 - Feature extraction runs as an independent, later pipeline step (Priority: P3)

**Goal**: Confirm `extract-features`/`generate-benchmark` never require `library_builder` to
run and never accumulate duplicate output on repeated runs — a property of the design from
US1/US2, validated here rather than newly implemented.

**Independent Test**: Run `extract-features`/`generate-benchmark` twice against
`zlib_feature_test/` and confirm exactly one `features.json`/`*.yaml` each time (no
duplicates, no rebuild of the native binary); confirm `harnessbuddy generate` never creates
either file in its own output directory.

### Implementation for User Story 3

- [ ] T021 [P] [US3] Test in `tests/feature_extractor/test_extraction.py`: running `extract-features` twice against `zlib_feature_test/` overwrites `features.json` in place (still exactly one file) and the second run reuses the cached native binary without rebuilding (FR-015, research.md §2) (depends on T011, T010) (bd: harnessbuddy-fk1.21)
- [ ] T022 [P] [US3] Test in `tests/test_cli.py`: running `harnessbuddy generate` (the existing `library_builder` pipeline) never creates or touches `features.json`/`*.yaml` in its output directory (depends on T012) (bd: harnessbuddy-fk1.22)

**Checkpoint**: All three user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T023 [P] Run `uv run ruff format`, `uv run ruff check`, `uv run ty check` over every new/changed Python module (`models.py`, `extraction.py`, `native_build.py`, `benchmark_yaml.py`, `cli.py`) and fix all warnings (bd: harnessbuddy-fk1.23)
- [ ] T024 [P] Run `clang-format` over `src/harnessbuddy/feature_extractor/native/`; confirm a clean build with `-Wall -Wextra -Werror -Wpedantic -Wshadow` (zero warnings) (bd: harnessbuddy-fk1.24)
- [ ] T025 Execute every step of `quickstart.md` against `zlib_feature_test/` end-to-end and confirm each expected outcome (bd: harnessbuddy-fk1.25)
- [ ] T026 [P] Update `plans/IDEAS.md` to mark `artifact_extractor` as implemented, cross-referencing `specs/006-feature-extractor/` (bd: harnessbuddy-fk1.26)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS US1 and US2.
- **US1 (Phase 3)**: Depends on Foundational. No dependency on US2/US3. This is the MVP.
- **US2 (Phase 4)**: Depends on Foundational, and functionally on US1 having produced a
  `features.json` to convert — but is implemented and merged independently of US1's
  completion status beyond that runtime input existing.
- **US3 (Phase 5)**: Depends on US1 (T011/T010) and US1's CLI registration (T012) — it
  validates properties of code US1/US2 already implement, so it has no independent
  implementation tasks of its own beyond tests.
- **Polish (Phase 6)**: Depends on all desired user stories being complete.

### Parallel Opportunities

- T002 and T004 (Phase 2) touch different files — parallel.
- T005 opens Phase 3; T006, T007, T008, and T010 each touch a different file and only depend
  on T005 — parallel once T005 lands.
- T013 and T014 (US1 tests) touch different files and both only depend on T012 — parallel.
- T015 (Phase 4, `pyproject.toml`) has no dependency on T002/T003 — can start as soon as
  Foundational lands, in parallel with all of Phase 3.
- T019 and T020 (US2 tests) touch different files and both only depend on T018 — parallel.
- T021 and T022 (US3) touch different files and are independent of each other — parallel.
- T023, T024, and T026 (Polish) touch disjoint file sets — parallel; T025 should run last
  since it exercises the fully-integrated CLI.

---

## Parallel Example: User Story 1

```bash
# After T005 (native/CMakeLists.txt) lands, launch together:
Task: "Implement json_writer.cpp/.hpp in src/harnessbuddy/feature_extractor/native/"
Task: "Implement extraction_action.cpp in src/harnessbuddy/feature_extractor/native/src/"
Task: "Implement macro_callbacks.cpp in src/harnessbuddy/feature_extractor/native/src/"
Task: "Implement native_build.py in src/harnessbuddy/feature_extractor/"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1 — `extract-features` against `zlib_feature_test/` producing
   a real, schema-valid `features.json`
4. **STOP and VALIDATE**: run `quickstart.md` steps 1 only
5. This is a usable, demoable increment on its own (a JSON artifact of a library's API surface)

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. Add US1 → validate independently → MVP
3. Add US2 → validate independently (needs US1's JSON as input, but is its own implementation)
4. Add US3 → validate independently (tests only, no new production code)
5. Polish

---

## Notes

- [P] tasks touch different files with no unmet dependencies.
- Every task is also tracked as a `bd` issue — see the `/speckit-tasks` completion report for
  the Txxx → bd-id mapping. Update status via `bd update <id> --claim` / `bd close <id>`, not
  by checking boxes in this file.
- `zlib_feature_test/` (repo root, untracked) must exist locally — see `quickstart.md`
  Prerequisites — before running any US1/US2/US3 test.
