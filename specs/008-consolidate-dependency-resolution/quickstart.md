# Quickstart: Validating Consolidated Library Dependency Resolution

## Prerequisites

- `uv sync` from repo root.
- No Docker or network access required (Constitution Principle IV).
- Capture a baseline first: `uv run pytest tests/test_cli.py tests/library_builder/ -q` on the
  pre-refactor code, and save the pass/fail list. The 4 pre-existing failures caused by the
  unrelated in-progress `scripts.py` edit (see `specs/007-.../quickstart.md`) are expected to
  remain exactly as they are — confirm that count doesn't change, don't try to fix them here.

## Scenario 1 — Zero behavior change (the load-bearing scenario for this feature)

Validates User Story 3 / FR-004 / SC-001 / SC-004.

1. Run `uv run pytest tests/test_cli.py tests/library_builder/ -q` after the refactor.
2. **Expected**: every test that passed before the refactor still passes, with **no
   modification to its assertions** — specifically the package/dependency tests already
   exercising specs/005 and specs/007 behavior:
   `test_generate_harness_unknown_linked_lib_warns_on_success`,
   `test_generate_library_and_harness_phase_share_package_without_duplication`,
   `test_generate_*_missing_package_reaches_*`,
   `test_generate_harness_agent_resolved_link_still_reports_package_on_success`.
3. If any of these required an assertion change to pass, the refactor changed observable
   behavior — stop and reconcile before proceeding, don't adjust the test to match.

## Scenario 2 — Old `state.json` still loads correctly

Validates FR-005 / Edge Case ("already-deployed state.json... must continue to load").

1. Write a `state.json` fixture matching today's pre-refactor shape, e.g.:
   ```json
   {
     "version": 1,
     "apt_packages": ["libssl-dev"],
     "brew_packages": ["openssl"],
     "unknown_libs": [],
     "sources": {"harness_agent": ["libssl-dev"]}
   }
   ```
2. Call the new module's `load_state()` on it.
3. **Expected**: returns a `DependencyState` with the same four fields populated identically —
   no exception, no silently dropped `sources` key.

## Scenario 3 — Adding a new discovery source touches one file

Validates User Story 1 / FR-003 / SC-002 / SC-003.

1. In a throwaway test, construct a `list[LibraryDependency]` by hand (simulating a
   hypothetical new discovery source) and call `dependency_resolution.merge()` directly —
   without touching `cli.py`.
2. **Expected**: the dependency lands in `DependencyState` correctly, de-duplicated against any
   pre-existing entry for the same library name, with no changes required anywhere in `cli.py`.

## Scenario 4 — Merge point is exhaustively covered by direct unit tests

Validates FR-001/FR-002/FR-007 and the correlation-gap decision in `research.md`.

New, direct (non-CLI, non-mocked-subprocess) unit tests for `dependency_resolution.py` should
cover, at minimum:
- Two sources report the same library name with complementary partial information (one has
  `link_flag` only, the other has `apt_package`/`brew_package` only) → merges into package lists
  correctly without losing either piece.
- A dependency with only `name` set (no known package on either platform) → lands in
  `unknown_libs`, not silently dropped.
- Calling `merge()` twice with the same dependencies → `apt_packages`/`brew_packages`/
  `unknown_libs`/`sources` are identical to calling it once (idempotency/de-dup).
- `from_agent_report` with more than one entry in `missing_libs` → documents (via test name/
  docstring, not a behavior change) that positional correlation is a known, unchanged limitation
  per `research.md`, not a new claim this refactor makes stronger.

## Full regression check

```bash
uv run ruff format && uv run ruff check && uv run ty check
uv run pytest tests/test_cli.py tests/library_builder/ -q
```

All four MUST pass with zero warnings (Constitution Principle I) before this feature is
considered done, with the pre-existing 4 `scripts.py`-related failures as the only accepted
exception (confirm the count and identity of failures is unchanged from the pre-refactor
baseline captured in Prerequisites).
