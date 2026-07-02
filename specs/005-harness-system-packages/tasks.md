---

description: "Task list for feature implementation"
---

# Tasks: Harness Linker Dependencies Become Install Commands

**Input**: Design documents from `specs/005-harness-system-packages/`
(`plan.md`, `research.md`, `data-model.md`, `contracts/generated-install-step.md`, `quickstart.md`)

**Tests**: Included — Constitution Principle IV requires behavior coverage for new
capability and error paths, and this project's existing tests
(`tests/library_builder/test_harness_explorer.py`, `tests/library_builder/test_package_names.py`,
`tests/test_cli.py`) already establish the fixture/mocking patterns to extend.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Maps to `spec.md` user stories (US1 = install commands cover every
  linked dependency regardless of exploration-host state, US2 = unmapped dependencies
  are surfaced, US3 = packages accumulate across pipeline stages without duplicates).
  Foundational/Polish tasks carry no story label.

## Path Conventions

Single project: `src/harnessbuddy/`, `tests/` at repository root (already in place — no
new top-level directories, no new dependencies, no new generated-file logic in
`local/generation.py` or `oss_fuzz/generation.py`, per `plan.md`'s Scale/Scope).

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: Add the pure helper every user-story change below calls. Per Constitution
Principle II, it lives next to `transitive_link_flags`'s producer in
`harness_explorer.py`, not in `cli.py`.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests for Foundational

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T001 [P] In `tests/library_builder/test_harness_explorer.py`, add a
  `# lib_names_from_link_flags` section importing `lib_names_from_link_flags` alongside
  the existing `explore_harness_compilation`, `reparse_lib_paths`, `reparse_link_config`
  import, with `test_lib_names_from_link_flags_strips_prefix` (asserts
  `lib_names_from_link_flags(["-lzstd", "-lz", "-llzma"]) == ["zstd", "z", "lzma"]`) and
  `test_lib_names_from_link_flags_empty_list` (asserts
  `lib_names_from_link_flags([]) == []`). Confirm both FAIL with an `ImportError` (the
  function doesn't exist yet).

### Implementation for Foundational

- [ ] T002 In `src/harnessbuddy/library_builder/harness_explorer.py`, add
  `lib_names_from_link_flags(flags: list[str]) -> list[str]` directly after
  `_extract_missing_system_libs` (before `_symbol_to_flag`): `return
  [flag.removeprefix("-l") for flag in flags]`, with a docstring noting every entry in
  `transitive_link_flags` is `-l<name>` (every key in `symbol_patterns.json` is
  `-l<name>`, confirmed in `research.md` Decision 3), matching the bare-name input
  `package_names.translate()` expects. Run T001 and confirm it now passes.

**Checkpoint**: `lib_names_from_link_flags` exists and is unit-tested; nothing calls it
yet. `uv run ty check` passes.

---

## Phase 2: User Story 1 - Install commands cover every linked dependency (Priority: P1) 🎯 MVP

**Goal**: Whenever the harness link step resolves a `-lxxx` flag — whether because the
linker reported it missing (`missing_system_libs`, already handled today) or because it
resolved silently since the exploration host already had the library
(`transitive_link_flags`, the gap this closes) — the corresponding apt/brew package
ends up in the generated Dockerfile and `setup.sh`.

**Independent Test**: Run HarnessBuddy against a repo whose harness resolves
`transitive_link_flags=["-lzstd", "-lz"]` with `missing_system_libs=[]` (i.e. the
libtiff scenario confirmed against `ground_truth_test_output/libtiff/`, where
`compile_harnesses.sh` embeds `-lzstd -lz -llzma` but neither `setup.sh` nor the
Dockerfile install anything). Confirm the generated Dockerfile and `setup.sh` both gain
install commands for `libzstd-dev`/`zlib1g-dev` (or their brew equivalents).

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T003 [P] [US1] In `tests/test_cli.py`, add
  `test_generate_harness_linked_flags_only_reaches_output_on_success`: patch
  `harnessbuddy.cli.build_harness` to return
  `HarnessExplorationResult(succeeded=True, command=[], static_libs=[],
  include_dir=Path("/tmp/install/include"), transitive_link_flags=["-lzstd", "-lz"],
  stdout="", stderr="", exit_code=0)` (note `missing_system_libs` defaults to `[]` —
  this is the exact bug scenario: the harness never failed, so today nothing gets
  translated). Run `main(["generate", str(local_repo_with_origin), "--output",
  str(output_dir)])`, assert `rc == 0`. Read `oss-fuzz/Dockerfile` and assert both
  `"libzstd-dev"` and `"zlib1g-dev"` appear (the Dockerfile always uses apt names,
  unaffected by the host platform running the test). Read `local/setup.sh` and branch on
  `sys.platform`: on `"darwin"`, assert `"zstd"` and `"zlib"` appear (this is the first
  place in the test suite where a harness-derived package populates `brew_packages`
  instead of relying on an agent's `missing_system_packages`, which never populates
  `brew_packages` — see `research.md`); otherwise assert `"libzstd-dev"` and
  `"zlib1g-dev"` appear. Confirm FAILS (Dockerfile/setup.sh currently contain no package
  lines for this scenario).
- [ ] T004 [P] [US1] In `tests/test_cli.py`, add
  `test_generate_agent_repaired_harness_linked_flags_reaches_output_on_success`,
  mirroring `test_generate_harness_missing_package_reaches_output_on_success`'s
  `fake_run_agent_streaming` fixture pattern: the fake agent writes
  `out/probe_harness`, and `compile_harnesses.sh` containing
  `EXTRA_LINK_FLAGS="-llzma"\n` (so `reparse_link_config` re-derives
  `transitive_link_flags=["-llzma"]` on the agent-success path in
  `invoke_harness_builder_agent`), and an `agent_report.json` with no
  `missing_system_packages`. Run `main([..., "--agent", "claude"])`, assert `rc == 0`.
  Assert `"liblzma-dev"` appears in the Dockerfile, and (platform-conditional as in T003)
  `"xz"` (brew) or `"liblzma-dev"` (apt) appears in `setup.sh`. This proves the fix
  covers `invoke_harness_builder_agent`'s success path, not only
  `explore_harness_compilation`'s. Confirm FAILS.

### Implementation for User Story 1

- [ ] T005 [US1] In `src/harnessbuddy/cli.py`'s `_run_harness_phase`: import
  `lib_names_from_link_flags` alongside the existing `translate as translate_packages`
  import. Replace the block
  ```python
  translation = None
  if harness_result.missing_system_libs:
      translation = translate_packages(harness_result.missing_system_libs)
      merge_packages_into_state(
          state,
          apt_packages=translation.apt_packages,
          brew_packages=translation.brew_packages,
          unknown_libs=translation.unknown_libs,
          source_tag="linker",
      )
      save_project_state(state_file, state)
  ```
  with a version that unions `harness_result.missing_system_libs` with
  `lib_names_from_link_flags(harness_result.transitive_link_flags)` (deduplicated via
  `list(dict.fromkeys(...))`) before deciding whether to translate/merge:
  ```python
  linked_libs = list(
      dict.fromkeys(
          harness_result.missing_system_libs
          + lib_names_from_link_flags(harness_result.transitive_link_flags)
      )
  )
  translation = None
  if linked_libs:
      translation = translate_packages(linked_libs)
      merge_packages_into_state(
          state,
          apt_packages=translation.apt_packages,
          brew_packages=translation.brew_packages,
          unknown_libs=translation.unknown_libs,
          source_tag="linker",
      )
      save_project_state(state_file, state)
  ```
  Leave the rest of the function (the `if not harness_result.succeeded:` warning block,
  which still reads `harness_result.missing_system_libs` specifically for the "missing
  system libraries" message — this is intentional, see `research.md` Decision 2) and the
  `analysis.system_packages = state["apt_packages"]` / `brew_packages = state[...]`
  lines unchanged. Run T003 and T004 and confirm both now pass; run `uv run pytest
  tests/test_cli.py -q` to confirm no regressions in the existing
  `test_generate_harness_missing_package_reaches_output_on_success` and
  `test_generate_harness_missing_package_reaches_state_then_next_run_output` tests.

**Checkpoint**: Any harness link step that resolves `-lxxx` flags — regardless of
whether the exploration host already had the library — produces the corresponding
apt/brew install commands in both generated outputs. This is a demonstrable,
independently valuable MVP on its own.

---

## Phase 3: User Story 2 - Unmapped dependencies are surfaced (Priority: P2)

**Goal**: When a linked dependency (from either `missing_system_libs` or
`transitive_link_flags`) has no entry in `package_names.json`, the user sees it named
explicitly, on both the success and failure paths — today `unknown_libs` is silently
written into `state.json` with no console output at all.

**Independent Test**: Run HarnessBuddy against a harness whose
`transitive_link_flags` includes a flag with no mapping (e.g. `-lnonexistentlib`).
Confirm the console output names `nonexistentlib` explicitly, even though the harness
link step succeeded.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T006 [P] [US2] In `tests/test_cli.py`, add
  `test_generate_harness_unknown_linked_lib_warns_on_success` (using the `capsys`
  fixture): patch `harnessbuddy.cli.build_harness` to return a succeeded
  `HarnessExplorationResult` with `transitive_link_flags=["-lnonexistentlib"]` and
  `missing_system_libs=[]`. Run `main([...])`, assert `rc == 0`, then assert
  `"nonexistentlib"` appears in `capsys.readouterr().err`. Confirm FAILS (no warning is
  printed today for `unknown_libs` on the success path — it is only ever written into
  `state.json`, never surfaced to the console, per `research.md`).

### Implementation for User Story 2

- [ ] T007 [US2] In `src/harnessbuddy/cli.py`'s `_run_harness_phase`, immediately after
  the `if linked_libs:` block from T005 (so it runs whenever `translation` was computed,
  on both the success and failure paths, unconditionally on
  `harness_result.succeeded`), add:
  ```python
  if translation is not None and translation.unknown_libs:
      unknown = ", ".join(translation.unknown_libs)
      print(
          f"Warning: no known apt/brew package mapping for: {unknown}. "
          "Install these manually before building elsewhere.",
          file=sys.stderr,
      )
  ```
  (`sys` is already imported in `cli.py`.) Run T006 and confirm it passes; run `uv run
  pytest tests/test_cli.py -q` to confirm no regressions.

**Checkpoint**: Every unmapped linked dependency is visibly reported, on both outcomes,
with zero silent drops.

---

## Phase 4: User Story 3 - Packages accumulate without duplicates (Priority: P3)

**Goal**: A package required by both the library-build phase and the harness-link phase
appears exactly once in each generated install command.

**Independent Test**: Run HarnessBuddy against a repo where the library-build phase
reports `missing_system_packages=["libzstd-dev"]` (agent-resolved) and the harness-link
phase independently resolves `transitive_link_flags=["-lzstd"]` (translating to the same
`libzstd-dev`). Confirm the Dockerfile's apt install line lists `libzstd-dev` exactly
once.

No new implementation — this story validates a property of the mechanism Phase 2
already built (`merge_packages_into_state` already deduplicates while preserving order
across `source_tag`s, per `data-model.md`), matching `spec.md`'s framing of US3 as a
correctness/hygiene guarantee rather than new behavior.

### Tests for User Story 3

- [ ] T008 [P] [US3] In `tests/test_cli.py`, add
  `test_generate_library_and_harness_phase_share_package_without_duplication`: patch
  `harnessbuddy.cli.build_library` to return a `BuildExplorationResult(..., llm_used=True,
  missing_system_packages=["libzstd-dev"])` (library-phase-contributed, already an
  apt-resolved name — mirroring the shape `agents.py`'s `invoke_library_builder_agent`
  produces) and patch `harnessbuddy.cli.build_harness` to return a succeeded
  `HarnessExplorationResult` with `transitive_link_flags=["-lzstd"]` (harness-phase
  contributed, translates to the same `libzstd-dev`). Run `main([...])`, assert `rc ==
  0`, then assert
  `Path(output_dir / "oss-fuzz" / "Dockerfile").read_text().count("libzstd-dev") == 1`.
  This is expected to PASS immediately once Phase 2 (T005) has landed — no new
  implementation task follows.

**Checkpoint**: Cross-phase package contributions collapse to one entry each, confirmed
by test rather than new code.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Confirm the full gate suite passes and record this work in the project's
progress log.

- [ ] T009 Run `uv run ruff format`, `uv run ruff check`, `uv run ty check`, and `uv run
  pytest -q`; fix any regression before proceeding.
- [ ] T010 [P] Manually run `quickstart.md` scenarios 1-4 and 6-7 against the real
  libtiff repository (no agent credentials needed); confirm the Dockerfile builds
  successfully in Docker (scenario 4) and no duplicate packages appear (scenario 6).
  Scenario 5 (unmapped dependency) can be exercised by temporarily deleting the `zstd`
  entry from a scratch copy of `package_names.json` and re-running.
- [ ] T011 [P] Add a short entry to `plans/oss-fuzz-builder/progress.md` recording this
  fix (mirroring the existing "Phase N —" section style), noting the libtiff
  ground-truth run that surfaced the gap.

**Checkpoint**: `ruff format --check`, `ruff check`, `ty check`, and `pytest -q` all pass
with zero warnings; `plan.md`'s Constitution Check remains fully passing.

---

## Dependencies & Execution Order

- **Phase 1 (Foundational)**: No dependencies — start immediately.
- **Phase 2 (US1)**: Depends on Phase 1 (`lib_names_from_link_flags` must exist before
  `cli.py` can call it). T003 and T004 (different scenarios, same file) can be written
  in parallel; T005 depends on both existing so it has failing tests to turn green.
- **Phase 3 (US2)**: Depends on Phase 2 being complete — the warning is added to the
  exact block T005 introduces.
- **Phase 4 (US3)**: Depends on Phase 2 being complete — validates a property of the
  mechanism T005 built. Independent of Phase 3 (no shared code path), so Phases 3 and 4
  can proceed in parallel once Phase 2 lands.
- **Phase 5 (Polish)**: Depends on Phases 2, 3, and 4 all being complete.

### Parallel Opportunities

- T001 has no sibling in Phase 1 — single task, but marked `[P]` relative to nothing
  (kept for format consistency; it is the only Foundational test task).
- T003 and T004 (different test functions, same file) — parallel to write.
- Once Phase 2 (T005) lands, T006 (US2) and T008 (US3) touch unrelated behavior in the
  same function and can be drafted in parallel, though both land in `tests/test_cli.py`
  alongside T003/T004 — expect one commit given the file overlap, matching this file's
  existing convention of many independent test functions in one file.
- T010 and T011 (independent, unrelated files) — parallel.

---

## Parallel Example: User Story 1 (Tests)

```bash
# Launch both test-writing tasks for User Story 1 together:
Task: "Add test_generate_harness_linked_flags_only_reaches_output_on_success to tests/test_cli.py"
Task: "Add test_generate_agent_repaired_harness_linked_flags_reaches_output_on_success to tests/test_cli.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Foundational (`lib_names_from_link_flags`).
2. Complete Phase 2: User Story 1 — every harness-linked dependency reaches the
   generated Dockerfile/setup.sh, regardless of exploration-host state.
3. **STOP and VALIDATE**: run `harnessbuddy generate` against libtiff and confirm
   `oss-fuzz/Dockerfile` builds in a clean Docker container (quickstart.md scenario 4).
4. Ship as MVP — US2 and US3 harden the same mechanism but the core gap described in
   the spec is already closed.

### Incremental Delivery

1. Foundational → helper ready.
2. Add User Story 1 → validate independently → closes the core gap (this alone is what
   the user asked for).
3. Add User Story 2 → validate independently → no more silent drops for unmapped
   dependencies.
4. Add User Story 3 → validate independently → proves the mechanism stays duplicate-free
   as more pipeline stages contribute packages.

### Recommended order

Sequential: Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5. Phase 3 and Phase 4 have no
data dependency on each other and could be reordered or parallelized across
contributors, but both depend on Phase 2's `_run_harness_phase` change landing first.
