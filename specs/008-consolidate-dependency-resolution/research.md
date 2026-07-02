# Phase 0 Research: Consolidate Library Dependency Resolution

## Current state (baseline for the refactor)

Traced directly against the codebase as it stands after specs/005 and specs/007:

- `cli.py` currently owns `_ProjectState` (a `TypedDict`), `_empty_state`, `load_project_state`,
  `save_project_state`, and `merge_packages_into_state` — all persisted-dependency-state
  plumbing living in the CLI orchestration file rather than the `library_builder` package.
- `merge_packages_into_state` is called from 5 sites in `cli.py`: `_run_library_phase` (agent
  success), `_run_harness_phase` (deterministic/"linker" translation, harness-agent success),
  and the two `BuildFailureError`/`LLMBudgetError` exception handlers in `_cmd_generate`
  (library and harness). Each site repeats the same shape: check whether a result/report has
  packages, call `merge_packages_into_state` with a hand-typed `source_tag` string, call
  `save_project_state`.
- `_run_harness_phase` additionally inlines the deterministic-path translation logic: computing
  `linked_libs` from `harness_result.missing_system_libs` and
  `lib_names_from_link_flags(harness_result.transitive_link_flags)`, calling
  `package_names.translate()`, and assembling the `apt_hint`/`brew_hint` strings for the console
  message — none of which is reused by the agent-report merge sites next to it.
- `harness_explorer.py`, `package_names.py`, `agents.py`, and `models.py` (`AgentReport`,
  `BuildExplorationResult`, `HarnessExplorationResult`) are otherwise unchanged from specs/007
  and already satisfy Constitution Principle VI (typed cross-module contracts, no loose dicts).

## Decision: new module owns state + the one merge point; existing typed results are untouched

**Decision**: Add `src/harnessbuddy/library_builder/dependency_resolution.py` owning:
`DependencySource` (enum), `LibraryDependency` (dataclass), `DependencyState` (dataclass
replacing the `_ProjectState` `TypedDict`), `load_state`/`save_state`, and one `merge()`
function. `cli.py` is reduced to calling this module's functions from its existing phase
functions — no new orchestration logic, no new source tags added inline.

**Rationale**: `_ProjectState`/`load_project_state`/`save_project_state`/
`merge_packages_into_state` are persisted-dependency-state concerns, not CLI argument-parsing
concerns — Constitution Principle II states `harnessbuddy.cli` "MUST stay limited to argument
parsing and dispatch — workflow logic belongs in the tool package it operates on." Moving them
into `library_builder` alongside the data they persist is both what closes User Story 1/2's
maintenance gap *and* what the existing constitution already requires independent of this
feature. Leaving `models.py`/`agents.py`/`harness_explorer.py`/`package_names.py` untouched
minimizes the blast radius for a change whose entire value is maintainability (User Story 3:
zero behavior change) — there is no requirement in specs/005 or 007 that those modules'
external shape change.

**Alternatives considered**:
- *Fold `dependency_resolution` into `package_names.py`*: rejected — `package_names.py` is a
  small, focused, already-correct static-translation-table module; growing it to also own
  merge/persistence conflates "translate a name" with "accumulate and de-duplicate results,"
  which is exactly the kind of scope creep Constitution Principle V warns against.
- *Change `AgentReport`'s wire format from three parallel lists
  (`missing_libs`/`missing_apt_packages`/`missing_brew_packages`) to a list of per-dependency
  objects*: rejected for this feature — see the correlation-gap decision below.

## Decision: the agent-report wire format stays three parallel lists (documented, unchanged limitation)

**Decision**: `agent_report.json`'s schema (three parallel arrays) is not changed by this
refactor. `dependency_resolution.py` zips `missing_libs[i]`/`missing_apt_packages[i]`/
`missing_brew_packages[i]` positionally when constructing `LibraryDependency` objects from an
`AgentReport` — the same de facto behavior the current code already has (each list is merged
independently today, with no guarantee of correspondence when a report contains more than one
dependency in the same run).

**Rationale**: This is a real, pre-existing gap — if an agent reports two different libraries'
packages in a single run, nothing today guarantees `missing_apt_packages[0]` corresponds to
`missing_libs[0]` rather than `missing_libs[1]`. But it has never manifested in an observed run
(every traced case, including the originating curl/openldap incident, involved exactly one
reported dependency), and specs/008's own FR-004/User Story 3 explicitly scope this feature to
*zero user-visible behavior change* beyond what specs/005 and 007 already require. Changing the
wire format would touch `agents/harness_builder/SKILL.md`'s external contract with the LLM
subprocess and is a genuine scope expansion, not a refactor.

**Alternatives considered**: Changing `agent_report.json` to `"dependencies": [{"lib": ...,
"apt": ..., "brew": ...}]` — rejected for this feature as scope creep; flagged below as a
follow-up.

**Follow-up (out of scope for this feature)**: If HarnessBuddy ever needs an agent to reliably
report *multiple* distinct dependencies with confidence in their name-to-package correlation,
the wire format should change to a list of objects at that point — not speculatively now
(Constitution Principle V).

## Decision: `DependencySource` is a `str, Enum` with today's exact string values

**Decision**: `class DependencySource(str, Enum): LINKER = "linker"; LIBRARY_AGENT =
"library_agent"; HARNESS_AGENT = "harness_agent"` — same three string values `state.json`
already persists today.

**Rationale**: Directly satisfies FR-005 (existing `state.json` files must keep loading) with
no migration: `json.dumps` on a `str` subclass Enum member serializes as the same string it
does today, and loading a dict whose keys are those same strings requires no conversion.
Simultaneously satisfies FR-006: every *code* call site now references
`DependencySource.HARNESS_AGENT` instead of typing `"harness_agent"` as a literal, so a typo is
a `NameError`/attribute-access failure caught immediately (and by `ty check` static analysis)
rather than silently creating a new, disconnected key in the persisted `sources` dict.

**Alternatives considered**: A plain `str` constant module (`SOURCE_HARNESS_AGENT = "..."`)
would also prevent typos, but an `Enum` additionally gives an exhaustiveness-checkable closed
set (FR-006's "closed, enumerated set" wording maps directly to an `Enum`, not to a set of
loose constants that nothing prevents from growing unbounded).

## Decision: `LibraryDependency` fields are all optional except `source`

**Decision**: `name: str | None`, `link_flag: str | None`, `apt_package: str | None`,
`brew_package: str | None` — all optional; only `source: DependencySource` is required.

**Rationale**: Directly required by FR-007 (a partially-resolved dependency — e.g. a link flag
with no known package yet — must not be discarded) and the corresponding Edge Case. A
dependency discovered by the deterministic prober before translation might have only `name`+
`link_flag`; after `package_names.translate()` runs, the same conceptual dependency gains
`apt_package`/`brew_package`. An agent-reported dependency might have only package names (no
bare name/`link_flag`, if it resolved the issue some other way). Modeling all fields as optional
lets `merge()` combine entries about "the same dependency" (matched by `name` when known, since
`name` is the identity key throughout — the whole reason `-lssl`/`libssl-dev`/`openssl` are
recognized as "the same thing" today via `package_names.json`) without forcing every producer to
populate fields it doesn't have.

**Alternatives considered**: Separate dataclasses per discovery stage — rejected, defeats FR-001
("a single internal representation ... used by every dependency-discovery mechanism").

## Decision: scope stays on the harness-side pipeline; library-build phase folds in only its two package fields

**Decision**: Per spec.md's own Assumptions, `dependency_resolution.py`'s `merge()`/
`DependencyState` are used by *both* phases (library-build and harness), but only the
harness-side callers construct `LibraryDependency` entries carrying `link_flag`. The
library-build phase's `missing_apt_packages`/`missing_brew_packages` (no `-l` flag concept,
confirmed in specs/007) construct `LibraryDependency` entries with `name=None`,
`link_flag=None`, only `apt_package`/`brew_package` set, tagged `DependencySource.LIBRARY_AGENT`.

**Rationale**: Reuses the same merge/persistence point for both phases (satisfying FR-002's
"exactly one function") without inventing a link-flag concept the library-build phase doesn't
have — matches the spec's explicit conditional ("only if doing so does not force an unused
concept onto that phase").

## Summary of module surface for `/speckit-tasks`

**Status: implemented.** `dependency_resolution.py` exists with exactly the surface described
below (`DependencySource` as `enum.StrEnum` rather than the literal `class X(str, Enum)` written
here — `ruff`'s `UP042` rule requires the former; both are functionally identical `str`
subclasses, so `state.json`'s on-disk shape is unaffected). `cli.py`'s five merge blocks and the
inlined `_run_harness_phase` translation logic were replaced with calls into this module exactly
as planned. Full regression run (`tests/test_cli.py tests/library_builder/ -q`) after the
refactor: 199 passed, with only the same 4 pre-existing `scripts.py`-related failures present
before this refactor — zero behavior change confirmed (User Story 3 / FR-004 / SC-001 / SC-004).

`src/harnessbuddy/library_builder/dependency_resolution.py`:
- `DependencySource` (`str, Enum`): `LINKER`, `LIBRARY_AGENT`, `HARNESS_AGENT`
- `LibraryDependency` (frozen dataclass): `source`, `name`, `link_flag`, `apt_package`,
  `brew_package` (all but `source` optional)
- `DependencyState` (dataclass): `apt_packages: list[str]`, `brew_packages: list[str]`,
  `unknown_libs: list[str]`, `sources: dict[str, list[str]]` — same shape `_ProjectState`
  persists today, moved here
- `load_state(path: Path) -> DependencyState` / `save_state(path: Path, state: DependencyState)`
  — replace `cli.load_project_state`/`save_project_state`
- `from_static_probe(missing_system_libs: list[str], transitive_link_flags: list[str]) ->
  list[LibraryDependency]` — wraps `lib_names_from_link_flags` + `package_names.translate()`,
  tags `DependencySource.LINKER`
- `from_agent_report(missing_libs, missing_apt_packages, missing_brew_packages, *, source:
  DependencySource) -> list[LibraryDependency]` — positional zip (see correlation-gap decision)
- `merge(state: DependencyState, dependencies: list[LibraryDependency]) -> None` — the one
  de-duplicating merge/persist point (FR-002), replacing `merge_packages_into_state`

`cli.py` changes: `_ProjectState`, `_empty_state`, `load_project_state`, `save_project_state`,
`merge_packages_into_state` removed; `_run_library_phase`/`_run_harness_phase`/`_cmd_generate`'s
two exception handlers call the new module's functions instead, with `state`'s type becoming
`DependencyState` throughout.
