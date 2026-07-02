# Phase 0 Research: Complete Library Dependency Packaging

## Context

This feature extends work already substantially implemented earlier in the same session that
raised `specs/007-complete-dependency-packaging/spec.md`:

- `AgentReport` (and `BuildExplorationResult`/`HarnessExplorationResult`) replaced the single,
  ambiguous `missing_system_packages` field with `missing_libs` (harness-only, bare `-l` name),
  `missing_apt_packages`, and `missing_brew_packages` — the agent now reports actual
  per-platform package names directly from its own knowledge, rather than one name blindly
  merged into `apt_packages` regardless of platform.
- `invoke_harness_builder_agent` (`agents.py`) synthesizes `-l<lib>` into `transitive_link_flags`
  for every bare name in `report.missing_libs`, and no longer wipes the pre-agent
  `missing_system_libs` to `[]` when the agent exits 0 but `_validate_harness_artifacts` finds
  no compiled binary (the bug that produced the blank "missing system libraries:" line and the
  wrong `openldap`-as-apt-package entry described in the spec's Story 1/3 motivation).
- Both `agents/library_builder/SKILL.md` and `agents/harness_builder/SKILL.md` were updated to
  ask for platform-specific package names directly, and dropped the old
  "write `install_packages.sh` to the source dir" instruction.

This research identifies what of the spec's four user stories is **already satisfied** by that
work, and what remains.

## Decision: the remaining gap is narrow — agent success-path package reporting

**Decision**: Story 1 ("every linked dependency reaches install commands, not only the ones
that were missing") is fully closed for the **deterministic** path already, and partially
closed for the **agent** path. The only remaining gap: when the harness-repair agent
successfully resolves a link failure by adding a `-lXXX` flag for a library that happened to
already be present on its own machine, nothing currently asks it to also report that library's
package name — `missing_apt_packages`/`missing_brew_packages` are only populated by the
"unresolvable failure" branch of `agents/harness_builder/SKILL.md` (step 5), not the general
"fix the problem" branch (step 4).

**Rationale**: Traced in `cli.py`'s `_run_harness_phase`: `linked_libs` is computed
unconditionally from `harness_result.missing_system_libs + lib_names_from_link_flags(harness_result.transitive_link_flags)`
and translated via `package_names.translate()` regardless of whether the harness build
ultimately succeeded. Since a successful agent fix causes `transitive_link_flags` to be
re-derived from the (possibly agent-edited) script text via `reparse_link_config`, any `-lXXX`
flag the agent adds — success or not — already flows into `linked_libs`. So *if* the library is
in `package_names.json`, it's already covered on success. The gap is specifically for libraries
the static table doesn't know about (the entire reason the agent path exists): `invoke_harness_builder_agent`
(`agents.py`) already returns `report.missing_apt_packages`/`report.missing_brew_packages`
unconditionally, regardless of `succeeded` — so the plumbing is already in place end to end.
The gap is entirely in `agents/harness_builder/SKILL.md`'s instructions: step 4 (the general
"fix the problem" path) never tells the agent to populate those fields — only step 5 (the
unresolvable-failure path) does. An agent that fixes a link by adding `-lXXX` for a library it
recognizes but the static table doesn't currently has no instructed reason to report that
library's package names, so it silently becomes an `unknown_libs` warning instead of a correct
install command.

**Alternatives considered**:
- *Rely solely on the deterministic reparse+translate step*: rejected — it can only resolve
  libraries already in `package_names.json`, which is exactly the set of libraries that never
  needed the agent in the first place.
- *Require `package_names.json` to cover every possible library*: rejected in this same session
  (see spec.md Assumptions) — doesn't scale, and is the specific design flaw already being
  moved away from.
- *Ask the agent to always report `missing_apt_packages`/`missing_brew_packages` for every
  `-lXXX` flag it touches, not only when a package must be installed*: **selected**. Minimal
  change (one more `SKILL.md` instruction + no schema change, since the fields already exist),
  and reuses the exact merge path already built (`agents.py` → `cli.py` → `state.json` →
  generators) for the failure case.

## Decision: field naming keeps its current "missing_" prefix

**Decision**: Do not rename `missing_libs`/`missing_apt_packages`/`missing_brew_packages` to
something like `linked_libs`/`linked_apt_packages`/`linked_brew_packages`, despite the fields
now also covering the "resolved without installing anything" case.

**Rationale**: The field still means the same thing from HarnessBuddy's point of view — "a
package this environment does not yet have recorded as installed, that the agent found while
resolving the link" — even when the *agent's own machine* happened to already have it. Renaming
buys no clarity and touches every call site (`agents.py`, `cli.py`, both `SKILL.md` files, all
tests) a second time in the same feature for no behavioral reason. This is revisited only if a
future need arises to distinguish "genuinely absent everywhere" from "absent on this specific
machine" — no such need exists today.

**Alternatives considered**: Renaming for clarity — rejected as churn without a behavioral
payoff (Constitution Principle V: no speculative renames ahead of a concrete need).

## Decision: `setup.sh`'s apt/brew branching in `local/generation.py` is correct as-is

**Decision**: `_write_setup_sh`'s `if sys.platform == "darwin" and brew_packages: ... elif
analysis.system_packages: ...` branching (apt and brew mutually exclusive, chosen by host
platform) is intentional and matches the spec's own assumption ("brew is only relevant to the
local setup.sh on macOS") — it is not a bug to fix under this feature.

**Rationale**: Investigated as part of validating current-state test coverage: four
`tests/test_cli.py` tests (`test_generate_*_missing_package_reaches_*`) failed on this
darwin-based development machine because their fixtures only supplied an apt package name
(`missing_apt_packages`) with no brew equivalent, so on macOS the (correct) brew-only branch
fired and the apt-only name never appeared in `setup.sh` — matching the platform, not a defect.
Confirmed these same four tests already failed identically on `main` prior to this session's
changes (same root cause, pre-existing), and are unrelated to an independent, already
in-progress, uncommitted edit to `scripts.py`'s `EXTRA_LINK_FLAGS` default (a missing closing
`}`) that separately breaks four other, unrelated tests
(`test_harness_build.py::TestZlibBuild`/`TestLibtiffBuild`, `test_scripts.py`) — that edit is
out of scope for this feature and was left untouched.

**Fix applied** (test-only, not a production code change): updated the four fixtures to supply
both `missing_apt_packages` and `missing_brew_packages`, and updated assertions to check the
platform-appropriate package name in `setup.sh` (`sys.platform == "darwin"` → brew name,
otherwise apt name), while the Dockerfile assertion stays apt-only unconditionally (Dockerfiles
always target Debian/Ubuntu per the existing 005 assumption).

## Decision: library-build agent gets no `-l` flag / `missing_libs` concept

**Decision**: Confirmed with the user directly earlier in this session and carried into this
spec's Assumptions: the library-build phase (`build_library.sh`) has no linker invocation
HarnessBuddy constructs, so there is nothing for a bare library name to attach to. Whatever the
resulting `.a` needs at link time is independently rediscovered by the harness-compile probe's
own undefined-symbol matching regardless of how the library was built. No change needed beyond
the `missing_apt_packages`/`missing_brew_packages` fields already added to that path.

**Alternatives considered**: Propagating a "the library-build agent noticed it might need libX
later" hint forward to the harness phase — rejected, no reliable guarantee such a hint would be
accurate (the feature might end up disabled, or be build-time-only), and the harness probe
already independently re-derives the true answer.

## Summary of remaining work for `/speckit-tasks`

**Status: implemented.** Both items below were completed directly (small enough to skip a
separate `/speckit-tasks` breakdown): `agents/harness_builder/SKILL.md` step 4 now instructs
the agent to report `missing_libs`/`missing_apt_packages`/`missing_brew_packages` for any new
`-lXXX` flag regardless of success, and
`tests/test_cli.py::test_generate_harness_agent_resolved_link_still_reports_package_on_success`
covers it end-to-end. No code changes were needed in `agents.py`/`cli.py`, confirming the
analysis below.

1. Extend `agents/harness_builder/SKILL.md` step 4 (the general "fix the problem" path, not
   only step 5's unresolvable-failure path) to also report `missing_libs` /
   `missing_apt_packages` / `missing_brew_packages` for any `-lXXX` flag newly added, whether or
   not installing a package was necessary to make it work. No code change is needed in
   `agents.py` — `invoke_harness_builder_agent` already returns `report.missing_apt_packages`/
   `report.missing_brew_packages` unconditionally regardless of `succeeded`, so once the prompt
   asks for this data on the success path, the existing merge/generation pipeline picks it up
   with no further changes.
2. Add regression coverage: an agent that succeeds by adding a `-lXXX` flag for an
   agent-known-but-table-unknown library, and reports that library's packages in
   `agent_report.json` alongside its success, still produces the correct install commands in
   both generated outputs (`Dockerfile` and `setup.sh`) even though `harness_result.succeeded`
   is `True`.
