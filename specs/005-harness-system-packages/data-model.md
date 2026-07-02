# Phase 1 Data Model: Harness Linker Dependencies Become Install Commands

This feature introduces no new persisted schema or dataclass. It widens the
data that flows through three entities that already exist in the codebase.
Each entity below maps to the Key Entities named in `spec.md`.

## Linked Dependency

**Spec entity**: An external library the compiled harness needs at link
time (e.g. `zstd`, `z`, `lzma`), identified independent of whether it was
missing on the exploration machine.

**Existing representation**: `HarnessExplorationResult` (models.py), two
overlapping fields:

| Field | Format | Populated when |
|-------|--------|-----------------|
| `transitive_link_flags` | `["-lzstd", "-lz", ...]` | Always, whenever `_resolve_flags` matches an undefined symbol to a known flag — independent of final success/failure. |
| `missing_system_libs` | `["zstd", "z", ...]` (bare names) | Only on the final failed exploration attempt, extracted from linker "not found" stderr. |

**Change**: No field changes. A new derivation step (bare name extraction
from `transitive_link_flags`, see research.md Decision 3) produces a list in
the same bare-name shape as `missing_system_libs`, so the two can be unioned
before translation. Validation: bare names are non-empty strings with no
`-l` prefix; the extraction is a pure string transform with no failure mode
(a flag not matching `-lxxx` — never produced today — would pass through
unchanged rather than erroring, since it isn't this feature's job to enforce
that invariant retroactively).

## Package Mapping

**Spec entity**: The association between a Linked Dependency and its
installable package name(s) for apt and brew.

**Existing representation**: `package_names.json` (`system_libs` set,
`mappings` dict) loaded into `_SYSTEM_LIBS` / `_MAPPINGS`, exposed via
`package_names.translate(lib_names: list[str]) -> PackageTranslation`
(`apt_packages`, `brew_packages`, `unknown_libs`).

**Change**: None. This feature is a consumer of `translate()`, not a
modifier of it. Growing the mapping table's coverage is explicitly out of
scope (see spec.md Assumptions).

## Generated Install Step

**Spec entity**: The apt/brew install command written into the Dockerfile
or setup.sh, covering the full, de-duplicated set of packages needed across
all pipeline stages.

**Existing representation**: `_ProjectState` (cli.py, persisted as
`state.json`): `apt_packages: list[str]`, `brew_packages: list[str]`,
`unknown_libs: list[str]`, `sources: dict[str, list[str]]`. Read into
`analysis.system_packages` / a local `brew_packages` variable before
`generate_local`/`generate_oss_fuzz` run.

**Change**: None to the schema or to the generation modules that render
this state into `RUN apt-get install ...` (Dockerfile) / `apt-get install
...` or `brew install ...` (setup.sh) lines — see contracts/generated-install-step.md.
The only change is that `_run_harness_phase` now calls
`merge_packages_into_state(...)` whenever the union of `missing_system_libs`
and flag-derived names is non-empty, instead of only when
`missing_system_libs` alone is non-empty.

## State Transitions

None — this is a linear data-flow widening (explore → derive bare names →
translate → merge → generate), not a stateful entity with transitions.
