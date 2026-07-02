# Phase 1 Data Model: Consolidate Library Dependency Resolution

All types below live in the new `src/harnessbuddy/library_builder/dependency_resolution.py`.
Existing dataclasses (`AgentReport`, `BuildExplorationResult`, `HarnessExplorationResult` in
`models.py`) are unchanged — this module consumes them, it does not replace them.

## `DependencySource` (enum)

```python
class DependencySource(str, Enum):
    LINKER = "linker"
    LIBRARY_AGENT = "library_agent"
    HARNESS_AGENT = "harness_agent"
```

Replaces free-text `source_tag: str` parameters at every current `merge_packages_into_state`
call site. `str` subclassing preserves today's exact `state.json` string values (FR-005/FR-006
— see `research.md`).

## `LibraryDependency` (frozen dataclass)

| Field | Type | Meaning | Populated by |
|---|---|---|---|
| `source` | `DependencySource` | Which pipeline stage discovered this | always |
| `name` | `str \| None` | Bare library name (no `-l` prefix), e.g. `"ssl"` | deterministic probe; agent when it reports `missing_libs` |
| `link_flag` | `str \| None` | e.g. `"-lssl"` | deterministic probe (from `transitive_link_flags`) |
| `apt_package` | `str \| None` | Debian/Ubuntu package name | deterministic probe (via `package_names.translate()`); agent (`missing_apt_packages`) |
| `brew_package` | `str \| None` | Homebrew formula name | deterministic probe (via `package_names.translate()`); agent (`missing_brew_packages`) |

Only `source` is required (FR-007 — partial resolution must not be discarded). `name` is the
identity key `merge()` uses to recognize "the same dependency" reported by more than one source
(mirrors how `package_names.json` already keys on bare name today).

## `DependencyState` (dataclass, replaces `cli._ProjectState`)

```python
@dataclass
class DependencyState:
    version: int = 1
    apt_packages: list[str] = field(default_factory=list)
    brew_packages: list[str] = field(default_factory=list)
    unknown_libs: list[str] = field(default_factory=list)
    sources: dict[str, list[str]] = field(default_factory=dict)
```

Identical on-disk shape to today's `_ProjectState`/`state.json` (FR-005). `sources` keys are
`DependencySource.value` strings on write; read back as plain strings (an old file's keys are
never migrated or rejected, only reproduced — an unrecognized key from a hypothetical future
renamed source would simply pass through untouched rather than crash).

## `merge(state: DependencyState, dependencies: list[LibraryDependency]) -> None`

The single consolidation point (FR-002). For each `LibraryDependency`:
1. If `apt_package` is set, union into `state.apt_packages` and into
   `state.sources[dep.source.value]` (both de-duplicated, order-preserving — same
   `dict.fromkeys` approach `merge_packages_into_state` already uses).
2. If `brew_package` is set, union into `state.brew_packages`.
3. If `name` is set but neither `apt_package` nor `brew_package` is known, union `name` into
   `state.unknown_libs` (replaces the current inline `translation.unknown_libs` handling in
   `cli.py`).

Mutates `state` in place, matching `merge_packages_into_state`'s current signature/semantics —
callers still explicitly call `save_state` afterward (no implicit I/O inside `merge()`, keeping
it a pure data operation that's easy to unit test without a filesystem).

## State Transitions

None new. `load_state` → zero or more `merge()` calls across a run (library phase, then harness
phase, then possibly an exception handler) → `save_state`, exactly mirroring today's
`load_project_state` → `merge_packages_into_state` (×1–2 per phase) → `save_project_state`
sequence — just through one shared module instead of `cli.py` inlining each step.

## Relationship to specs/007's data model

`specs/007-complete-dependency-packaging/data-model.md` already identified "Library Dependency,"
"Package Resolution," and "Generated Install Step" as the conceptual entities. This feature
gives them one concrete shared type (`LibraryDependency`) and one concrete merge point
(`merge()`) instead of the parallel-fields-plus-scattered-blocks implementation 007 left in
place. No spec-007 functional requirement changes; FR-008 explicitly requires this.
