# Contract: `dependency_resolution` module public API

This is the internal interface every current and future dependency-discovery source (and
`cli.py`'s orchestration code) programs against. There is no external wire format change in
this feature (see `research.md`'s agent-report-format decision) — this contract is the
in-process API surface, the project-appropriate contract type for an internal library module
(Constitution Principle II: cross-module contracts MUST be typed dataclasses, not loose dicts).

## Producers → `list[LibraryDependency]`

```python
def from_static_probe(
    missing_system_libs: list[str], transitive_link_flags: list[str]
) -> list[LibraryDependency]: ...

def from_agent_report(
    missing_libs: list[str],
    missing_apt_packages: list[str],
    missing_brew_packages: list[str],
    *,
    source: DependencySource,
) -> list[LibraryDependency]: ...
```

- `from_static_probe` is called once per harness phase, from the deterministic probe's result
  fields — replaces `_run_harness_phase`'s inline `linked_libs`/`translate_packages()` block.
- `from_agent_report` is called wherever an `AgentReport`-derived result (or the report itself,
  on the `BuildFailureError`/`LLMBudgetError` exception paths) needs converting — replaces every
  `if result.missing_apt_packages or result.missing_brew_packages: merge_packages_into_state(...)`
  block. `source` is supplied by the caller (`DependencySource.LIBRARY_AGENT` or
  `DependencySource.HARNESS_AGENT`) since the module has no way to know which phase called it.

**Contract**: both functions are pure — no I/O, no mutation of arguments, always return a new
list (possibly empty). Callers own deciding *whether* to call `save_state` afterward.

## Consolidation point

```python
def merge(state: DependencyState, dependencies: list[LibraryDependency]) -> None:
```

**Contract**: idempotent — merging the same `dependencies` list twice produces the same
`state` as merging it once (matches `merge_packages_into_state`'s existing `dict.fromkeys`
de-duplication guarantee, FR-008). Mutates `state` in place; does not persist it. This is the
**only** function in the codebase permitted to write to `DependencyState.apt_packages`/
`brew_packages`/`unknown_libs`/`sources` (FR-002/FR-003) — a code reviewer can grep for direct
list mutation of those fields outside this function as a lint-by-convention check.

## Persistence

```python
def load_state(path: Path) -> DependencyState: ...
def save_state(path: Path, state: DependencyState) -> None: ...
```

**Contract**: `load_state` on a missing or malformed file returns `DependencyState()` (today's
`_empty_state()` behavior) rather than raising — callers never need a try/except around it.
`load_state(path)` on a file written by `save_state` (this version or the pre-refactor
`save_project_state`) MUST produce an equivalent `DependencyState` (FR-005) — round-trip
equivalence with the pre-refactor on-disk format is the compatibility bar, verified by loading
a fixture `state.json` captured from before this refactor.

## Consumers

- `cli.py`'s `_run_library_phase`, `_run_harness_phase`, and the two exception handlers in
  `_cmd_generate` — call the producer functions, then `merge()`, then `save_state()`.
- `local/generation.py` and `oss_fuzz/generation.py` are unaffected — they still receive plain
  `list[str]` (`analysis.system_packages`, `brew_packages`) exactly as today; `DependencyState`
  does not leak past `cli.py`.

## Compatibility

Not a wire-format or on-disk-format change — see `research.md`. This contract governs only
which internal functions other code is expected to call; it introduces no user-visible or
cross-process compatibility concern.
