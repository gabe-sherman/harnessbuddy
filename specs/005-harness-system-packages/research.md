# Phase 0 Research: Harness Linker Dependencies Become Install Commands

No `NEEDS CLARIFICATION` markers remain in the Technical Context — this
research documents the design decisions made while resolving how to close
the gap described in the spec, grounded in the current implementation.

## Decision 1: Where to widen the input to package translation

**Decision**: Widen the single existing call site in `cli.py`'s
`_run_harness_phase` that already translates and merges harness-derived
packages, rather than changing `explore_harness_compilation` or
`invoke_harness_builder_agent` individually.

**Rationale**: `_run_harness_phase` is the sole consumer of the final
`HarnessExplorationResult`, regardless of whether `build_harness` took the
deterministic path (`explore_harness_compilation`) or the agent-repair path
(`invoke_harness_builder_agent`). It already owns the
translate-then-`merge_packages_into_state`-then-`save_project_state`
sequence for `missing_system_libs` and `missing_system_packages`. Widening
its input is a one-place change that automatically covers both the
deterministic and agent-repaired cases.

**Alternatives considered**:
- Populate `missing_system_libs` from `transitive_link_flags` inside
  `explore_harness_compilation` on the success path too. Rejected: it would
  require the same fix a second time inside `invoke_harness_builder_agent`
  (which independently sets `missing_system_libs = []` on success), and it
  would conflate two distinct signals under one field name — see Decision 2.

## Decision 2: Keep `missing_system_libs` semantics unchanged

**Decision**: Do not repurpose `HarnessExplorationResult.missing_system_libs`
to also carry "linked regardless of host state" information. Introduce that
as a separate derivation from `transitive_link_flags` at the merge point,
and union the two lists there.

**Rationale**: `missing_system_libs` today means specifically "the linker on
*this* machine reported these as not found" — it is used verbatim in the
`_run_harness_phase` failure-path warning message and passed into
`agents.py`'s `build_harness_prompt` as `missing_system_libs (linker-reported)`
to give the harness-repair agent an accurate signal. Overloading it with
"everything the harness links against" would make that message and prompt
misleading (e.g. reporting zstd as "missing" when it linked successfully).

**Alternatives considered**:
- Rename/repurpose the field. Rejected: touches `agents.py`'s prompt
  construction and the failure-path print statement for no behavioral
  benefit, and increases the diff without narrowing the actual gap.

## Decision 3: Bare-library-name extraction lives next to the flag producer

**Decision**: Add a small pure function in `harness_explorer.py` (alongside
`_extract_missing_system_libs`) that converts a list of `-lxxx` flags into
bare library names (e.g. `-lzstd` -> `zstd`), for feeding into
`package_names.translate()`.

**Rationale**: All entries in `transitive_link_flags` are produced by
`_symbol_to_flag`, whose possible values are exactly the `-lxxx` keys in
`symbol_patterns.json` (verified: every key is `-l<name>`, no other flag
shapes are produced). `harness_explorer.py` already owns this format
assumption via `_STATIC_LIB_ENTRY_RE`/`_EXTRA_LINK_FLAGS_RE`, so a new
one-line stripping helper belongs there, keeping `cli.py` limited to
orchestration per Constitution Principle II.

**Alternatives considered**:
- Inline `flag.removeprefix("-l")` directly in `cli.py`. Rejected: `cli.py`
  is meant to stay a thin dispatch layer; a format assumption about link
  flags belongs with the module that defines that format.
- Teach `package_names.translate()` to accept raw `-lxxx` flags directly.
  Rejected: unnecessary API surface per Constitution Principle V — the
  translator's contract (bare library names in, package names out) is
  already correct and used elsewhere; only the caller's input was incomplete.

## Decision 4: No changes to `local/generation.py` or `oss_fuzz/generation.py`

**Decision**: Make no changes to the generation modules that write
`setup.sh` and the `Dockerfile`.

**Rationale**: Both already consume `analysis.system_packages` (apt) and
`brew_packages` (macOS) correctly — `_write_setup_sh` and the Dockerfile
writer in `oss_fuzz/generation.py` already emit the right install lines
whenever those lists are non-empty. The gap is entirely upstream, in what
populates those lists before generation runs.

**Alternatives considered**: None — confirmed by reading both modules; no
alternative design was needed here.

## Decision 5: Deduplication and merge-order

**Decision**: Reuse `merge_packages_into_state` unchanged; union
`missing_system_libs` and the newly-derived bare names from
`transitive_link_flags` into one list (de-duplicated) before calling
`package_names.translate()` once per harness phase run.

**Rationale**: `merge_packages_into_state` already deduplicates while
preserving order across calls from different `source_tag`s (`library_agent`,
`linker`, `harness_agent`), satisfying FR-005/FR-008 (no duplicate packages,
packages preserved across pipeline stages) with no new code.

**Alternatives considered**:
- Two separate `translate()`/`merge_packages_into_state()` calls (one for
  `missing_system_libs`, one for the flag-derived names). Rejected: would
  produce two `sources["linker"]` entries to reconcile for no benefit, since
  both ultimately come from the same harness-exploration step and the same
  `source_tag` is already used for both today.
