# Phase 1 Data Model: Complete Library Dependency Packaging

HarnessBuddy has no database — "data model" here means the typed dataclasses and persisted
JSON state that carry dependency information between pipeline stages. This document maps the
spec's Key Entities onto the concrete types that already exist (mostly unchanged by this
feature) plus the one call-site behavior that changes.

## Library Dependency

Represented by two related but distinct fields, both already present:

- `HarnessExplorationResult.missing_system_libs: list[str]` (`models.py`) — bare library
  names (no `-l` prefix) the deterministic probe's linker output or an agent's diagnosis
  identified as unresolved. Populated by `_extract_missing_system_libs` (`harness_explorer.py`,
  regex over real linker stderr) and merged with `AgentReport.missing_libs` in
  `invoke_harness_builder_agent` (`agents.py`).
- `HarnessExplorationResult.transitive_link_flags: list[str]` (`models.py`) — `-l<name>` flags
  already encoded into the harness link command, whether resolved by
  `symbol_patterns.json` matching, re-parsed from an agent-edited script
  (`reparse_link_config`), or synthesized from `AgentReport.missing_libs` in `agents.py`.

Both are library-identity fields; a given library can appear in one, the other, or both,
depending on whether it was ever *unresolved* vs. merely *linked*.

## Package Resolution

- `AgentReport.missing_apt_packages: list[str]` / `AgentReport.missing_brew_packages: list[str]`
  (`models.py`) — the agent's own direct knowledge of the installable package name per platform,
  parsed from `agent_report.json` by `read_agent_report` (`exploration.py`). No schema change
  needed for this feature — see `research.md` for why the remaining gap is prompt-only.
- `BuildExplorationResult.missing_apt_packages` / `missing_brew_packages` and
  `HarnessExplorationResult.missing_apt_packages` / `missing_brew_packages` (`models.py`) — the
  same values, copied onto the phase result so `cli.py` doesn't need to reach into `AgentReport`
  directly.
- `package_names.PackageTranslation` (`package_names.py`) — the deterministic-path equivalent:
  `apt_packages` / `brew_packages` / `unknown_libs`, produced by `translate()` from a static
  `package_names.json` mapping, for libraries the symbol-pattern prober already recognizes.

Both resolution sources feed the same downstream sink (below) without the caller needing to
know which one produced a given package name.

## Generated Install Step

- `_ProjectState` (`cli.py`, a `TypedDict`) — `apt_packages: list[str]`, `brew_packages:
  list[str]`, `unknown_libs: list[str]`, and `sources: dict[str, list[str]]` (per-tag
  provenance: `"library_agent"`, `"linker"`, `"harness_agent"`). Persisted to
  `.harnessbuddy/<project>/state.json` via `save_project_state`/`load_project_state`, so
  packages discovered on one run (including one that ends in `BuildFailureError`) are still
  available to the next run's output generation.
- `merge_packages_into_state` (`cli.py`) — the single de-duplicating union point (FR-008/FR-009):
  every call site (library-agent success, harness-agent success, harness deterministic
  translation, and both `BuildFailureError`/`LLMBudgetError` exception handlers) routes through
  this one function, so no call site can independently clobber another's contribution.
- `AnalysisResult.system_packages: list[str]` (`models.py`) — set from `state["apt_packages"]`
  immediately before generation (`cli.py`), consumed by
  `oss_fuzz/generation.py`'s Dockerfile `RUN apt-get install` line.
- `brew_packages: list[str]` (plain list, not on a dataclass) — read from
  `state["brew_packages"]` in `_run_harness_phase` (`cli.py`), threaded into
  `local/generation.py`'s `_write_setup_sh`.

## State Transitions

No new transitions. The existing flow already satisfies FR-008/FR-009 (dedup, no
overwrite-on-later-failure) via `merge_packages_into_state`'s additive `dict.fromkeys` union
and the `agents.py` fix (this session) that stopped a validation-only harness-agent failure from
resetting `missing_system_libs` to `[]`. The only behavior this feature still needs to add is
**which fields get populated when** — specifically, `agent_report.json` gaining
`missing_apt_packages`/`missing_brew_packages` values on an agent's *successful* fix path, per
`research.md`'s remaining-work item — not a new field, table, or transition.
