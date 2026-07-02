---

description: "Task list for Consolidate Library Dependency Resolution"
---

# Tasks: Consolidate Library Dependency Resolution

**Input**: Design documents from `specs/008-consolidate-dependency-resolution/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/dependency_resolution_api.md, quickstart.md

**Tests**: Included — Constitution Principle IV (Test-First, Behavior-Focused Testing) is a
non-negotiable project principle, not a per-feature opt-in.

**Organization**: Tasks are grouped by user story (all P1 for this refactor — see spec.md).
User Story 3 (zero behavior change) functions as the regression gate for Stories 1 and 2 rather
than an independent increment, since this is a consolidation of existing behavior, not new
functionality.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files or independent test cases, no dependency on an
  incomplete task)
- **[Story]**: US1 (new-source extensibility), US2 (traceability), US3 (zero behavior change)
- Every task lists its exact file path

---

## Phase 1: Setup

**Purpose**: Scaffold the new module location.

- [ ] T001 Create `src/harnessbuddy/library_builder/dependency_resolution.py` with a module
      docstring describing its role (the single dependency-resolution/merge point replacing the
      scattered logic in `cli.py`, per `research.md`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared types and core operations every user story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T002 [P] Define `DependencySource(str, Enum)` in
      `src/harnessbuddy/library_builder/dependency_resolution.py` with members `LINKER`,
      `LIBRARY_AGENT`, `HARNESS_AGENT` matching today's exact `state.json` string values
      (`research.md` Decision: enum design)
- [ ] T003 [P] Define the frozen `LibraryDependency` dataclass in
      `src/harnessbuddy/library_builder/dependency_resolution.py` (`source`, `name`,
      `link_flag`, `apt_package`, `brew_package` — only `source` required; see `data-model.md`)
- [ ] T004 [P] Define the `DependencyState` dataclass in
      `src/harnessbuddy/library_builder/dependency_resolution.py` (`version`, `apt_packages`,
      `brew_packages`, `unknown_libs`, `sources` — identical shape to today's
      `cli._ProjectState`; see `data-model.md`)
- [ ] T005 [P] Write unit tests for `load_state`/`save_state` in
      `tests/library_builder/test_dependency_resolution.py`: missing file → empty
      `DependencyState`, malformed JSON → empty `DependencyState`, round-trip preserves all four
      fields, and loading a fixture matching today's pre-refactor `state.json` shape (free-text
      `sources` keys) round-trips unchanged (quickstart Scenario 2) — write these before T006
- [ ] T006 Implement `load_state(path: Path) -> DependencyState` and
      `save_state(path: Path, state: DependencyState) -> None` in
      `src/harnessbuddy/library_builder/dependency_resolution.py` to make T005 pass
- [ ] T007 [P] Write unit tests for `merge(state, dependencies)` in
      `tests/library_builder/test_dependency_resolution.py`: two sources reporting the same
      library name with complementary partial info merge into one entry; a `name`-only
      dependency with no known package lands in `unknown_libs`; calling `merge()` twice with the
      same input is idempotent (quickstart Scenario 4) — write these before T008
- [ ] T008 Implement `merge(state: DependencyState, dependencies: list[LibraryDependency]) ->
      None` in `src/harnessbuddy/library_builder/dependency_resolution.py` to make T007 pass

**Checkpoint**: Core module complete and independently unit-tested — user story work can begin.

---

## Phase 3: User Story 1 - Adding a new dependency-discovery source touches one place (Priority: P1)

**Goal**: `cli.py`'s five near-duplicate merge blocks are replaced by calls into
`dependency_resolution`'s producer functions and the single `merge()` point.

**Independent Test**: quickstart.md Scenario 3 — a hand-built `list[LibraryDependency]` merges
correctly via `dependency_resolution.merge()` alone, with no `cli.py` changes required to
accommodate it.

### Tests for User Story 1

- [ ] T009 [P] [US1] Write unit tests for `from_static_probe(missing_system_libs,
      transitive_link_flags)` in `tests/library_builder/test_dependency_resolution.py`: resolves
      known libraries via `package_names.translate()`, tags results `DependencySource.LINKER`,
      and places an unmapped library into the unknown-lib case — write before T011
- [ ] T010 [P] [US1] Write unit tests for `from_agent_report(missing_libs,
      missing_apt_packages, missing_brew_packages, source=...)` in
      `tests/library_builder/test_dependency_resolution.py`: single-dependency reports zip
      correctly; a docstring/test-name-documented case notes the positional-correlation
      limitation for multi-dependency reports is unchanged from today (`research.md`
      correlation-gap decision, not a new guarantee) — write before T012

### Implementation for User Story 1

- [ ] T011 [US1] Implement `from_static_probe()` in
      `src/harnessbuddy/library_builder/dependency_resolution.py`, wrapping
      `harness_explorer.lib_names_from_link_flags()` and `package_names.translate()` (depends on
      T009)
- [ ] T012 [US1] Implement `from_agent_report()` in
      `src/harnessbuddy/library_builder/dependency_resolution.py` (depends on T010)
- [ ] T013 [US1] Replace `_run_library_phase` in `src/harnessbuddy/cli.py` to build
      dependencies via `dependency_resolution.from_agent_report(..., source=DependencySource.LIBRARY_AGENT)`
      and call `dependency_resolution.merge()` + `save_state()` instead of
      `merge_packages_into_state`
- [ ] T014 [US1] Replace `_run_harness_phase` in `src/harnessbuddy/cli.py`: the deterministic
      translation block becomes `dependency_resolution.from_static_probe(...)`, the harness-agent
      block becomes `dependency_resolution.from_agent_report(..., source=DependencySource.HARNESS_AGENT)`,
      both merged via one `dependency_resolution.merge()` call, and the console message's
      apt/brew hint lists rebuilt by reading back from the merged `DependencyState` instead of
      the removed `translation` object
- [ ] T015 [US1] Replace the two `BuildFailureError`/`LLMBudgetError` exception handlers in
      `_cmd_generate` (`src/harnessbuddy/cli.py`) to build dependencies via
      `dependency_resolution.from_agent_report()` (tagged `LIBRARY_AGENT` / `HARNESS_AGENT`
      respectively) and call `merge()` + `save_state()`
- [ ] T016 [US1] Remove `_ProjectState`, `_empty_state`, `load_project_state`,
      `save_project_state`, and `merge_packages_into_state` from `src/harnessbuddy/cli.py` (dead
      code once T013-T015 land)
- [ ] T017 [US1] Write the extensibility test from quickstart Scenario 3 in
      `tests/library_builder/test_dependency_resolution.py`: construct a `LibraryDependency` by
      hand (simulating a hypothetical new discovery source) and confirm it merges correctly via
      `dependency_resolution.merge()` alone

**Checkpoint**: `cli.py` reduced to orchestration/dispatch; a new dependency-discovery source
can be added by producing `LibraryDependency` entries and calling `merge()`, with no `cli.py`
change required.

---

## Phase 4: User Story 2 - Tracing why a package did or didn't reach the generated output (Priority: P1)

**Goal**: Every persisted package is attributable to exactly one enumerated `DependencySource`,
and a misspelled/unrecognized tag can no longer silently create a disconnected bucket.

**Independent Test**: quickstart.md Scenario 2 — loading a pre-refactor `state.json` fixture
produces identical, correctly attributed results.

### Tests for User Story 2

- [ ] T018 [P] [US2] Write a test in `tests/library_builder/test_dependency_resolution.py`
      confirming `DependencyState.sources` keys equal `DependencySource.value` exactly for each
      producer path (`from_static_probe` → `"linker"`, `from_agent_report(source=LIBRARY_AGENT)`
      → `"library_agent"`, `from_agent_report(source=HARNESS_AGENT)` → `"harness_agent"`)
- [ ] T019 [US2] Write the backward-compatibility test from quickstart Scenario 2 in
      `tests/library_builder/test_dependency_resolution.py`: a fixture `state.json` matching
      today's pre-refactor shape (free-text `sources` keys) loads via `load_state()` into an
      identical `DependencyState`

### Implementation for User Story 2

- [ ] T020 [US2] Address any gap T018/T019 surface in `load_state()`/`save_state()` (expected to
      be none, given T006's design — this task exists to close the loop if a gap is found, not
      to add new speculative handling)

**Checkpoint**: Source attribution is closed-enum-based and old `state.json` files remain fully
compatible.

---

## Phase 5: User Story 3 - Refactor introduces zero user-visible behavior change (Priority: P1)

**Goal**: Every existing test that asserts on generated output or console messages passes with
unmodified assertions; only tests that directly exercised now-relocated functions change their
call target.

**Independent Test**: quickstart.md Scenario 1 — full pre-existing suite, diffed against a
pre-refactor baseline.

- [ ] T021 [US3] Migrate the "load_project_state / save_project_state /
      merge_packages_into_state" test block from `tests/test_cli.py` (the ~8 tests under that
      comment, e.g. `test_load_project_state_absent_returns_empty`,
      `test_save_and_load_project_state_roundtrip`) into
      `tests/library_builder/test_dependency_resolution.py`, updating only the imports and call
      targets (`load_state`/`save_state`/`merge`) — behavioral assertions stay the same
- [ ] T022 [US3] Remove the now-unused `load_project_state`/`save_project_state`/
      `merge_packages_into_state` import block and the migrated test functions from
      `tests/test_cli.py`
- [ ] T023 [US3] Run `uv run pytest tests/test_cli.py tests/library_builder/ -q` and confirm:
      every test that was passing before this feature (captured as a baseline per
      quickstart.md's Prerequisites) still passes with unmodified assertions, and the only
      failures are the 4 pre-existing, unrelated `scripts.py`-caused ones already documented in
      `specs/007-complete-dependency-packaging/quickstart.md`

**Checkpoint**: Zero regressions confirmed; refactor is behavior-preserving.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T024 [P] Run `uv run ruff format && uv run ruff check && uv run ty check` across all
      touched files (`src/harnessbuddy/library_builder/dependency_resolution.py`,
      `src/harnessbuddy/cli.py`, `tests/library_builder/test_dependency_resolution.py`,
      `tests/test_cli.py`) and resolve every warning
- [ ] T025 [P] Update `specs/008-consolidate-dependency-resolution/research.md`'s "Summary of
      module surface" section to note implementation is complete, mirroring how
      `specs/007-complete-dependency-packaging/research.md` was marked
- [ ] T026 Run the full quickstart.md "Full regression check" and record the result in the PR
      description or session notes

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Phase 1. Blocks all user stories — `DependencySource`/
  `LibraryDependency`/`DependencyState`/`load_state`/`save_state`/`merge` must exist before
  either producer function (US1) or the traceability tests (US2) can be written.
- **User Story 1 (Phase 3)**: Depends on Phase 2. Not depended on by US2, but US3's regression
  gate depends on US1's `cli.py` rewiring being complete (there is nothing to regression-test
  until the call sites actually change).
- **User Story 2 (Phase 4)**: Depends on Phase 2 only — can run in parallel with Phase 3, since
  it tests the foundational module's enum/persistence behavior directly, not `cli.py`.
- **User Story 3 (Phase 5)**: Depends on Phase 3 (US1) being complete — it is the regression
  gate for the `cli.py` rewiring.
- **Polish (Phase 6)**: Depends on Phases 3, 4, and 5 all being complete.

### Parallel Opportunities

- T002/T003/T004 (type definitions) can be written in parallel — different classes in the same
  new, currently-empty file, but no data dependency between them.
- T005 and T007 (test-writing) can proceed in parallel with each other, and with T002-T004,
  since they test the interfaces T002-T004 define, not their implementations.
- Phase 3 (US1) and Phase 4 (US2) can be worked in parallel once Phase 2 completes — US2 only
  touches the foundational module's own tests, not `cli.py`.
- T024/T025 in Polish can run in parallel with each other.

---

## Parallel Example: Foundational Phase

```bash
Task: "Define DependencySource enum in dependency_resolution.py"
Task: "Define LibraryDependency dataclass in dependency_resolution.py"
Task: "Define DependencyState dataclass in dependency_resolution.py"
```

## Parallel Example: User Story 1 tests

```bash
Task: "Write unit tests for from_static_probe() in test_dependency_resolution.py"
Task: "Write unit tests for from_agent_report() in test_dependency_resolution.py"
```

---

## Implementation Strategy

### Suggested single-pass order (this is a cohesive refactor, not an incremental-delivery feature)

Unlike a typical feature where User Story 1 alone is a shippable MVP, this refactor's value only
lands once `cli.py` is actually rewired (US1) and proven behavior-identical (US3) — shipping
only Phase 2 (the new module, unused) delivers nothing. Recommended order:

1. Phase 1 + Phase 2 (Setup + Foundational) — the shared module, fully unit-tested in isolation.
2. Phase 3 (US1) — rewire `cli.py`; this is where the maintainability payoff actually lands.
3. Phase 4 (US2) — can be done in parallel with Phase 3 if staffed, or immediately after.
4. Phase 5 (US3) — the regression gate; run last, since it needs Phase 3's rewiring to test
   against.
5. Phase 6 (Polish) — lint/type/full-suite sign-off.

### Parallel Team Strategy

With two contributors: one takes Phase 3 (US1, the `cli.py` rewiring) while the other takes
Phase 4 (US2, traceability/compat tests) once Phase 2 lands — they touch different files
(`cli.py` vs. test-only additions to `test_dependency_resolution.py`) until Phase 5's regression
gate, which should be run by whoever finishes Phase 3.

---

## Notes

- All three user stories are P1 because this is a tightly-scoped internal refactor, not a
  feature with genuinely independent increments — see spec.md's Assumptions.
- Commit after each phase checkpoint, not after every individual task — a partially-rewired
  `cli.py` (e.g., only `_run_library_phase` migrated) is not an independently useful commit
  boundary for this feature.
- Per quickstart.md: capture the pre-refactor test pass/fail baseline **before** starting T001,
  so T023's "zero regressions" claim has something concrete to diff against.
