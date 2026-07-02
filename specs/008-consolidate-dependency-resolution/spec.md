# Feature Specification: Consolidate Library Dependency Resolution

**Feature Branch**: `008-consolidate-dependency-resolution`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "Consolidate HarnessBuddy's scattered library-dependency-resolution logic into a single, well-defined module. Currently, the question \"how does a dependency become an apt/brew install command or a -l link flag\" is answered by four separate places that have grown independently: harness_explorer.py's symbol-pattern matching, package_names.py's static apt/brew translation table, agents.py's agent-self-report handling (missing_libs/missing_apt_packages/missing_brew_packages) and -l flag synthesis, and cli.py's _run_harness_phase, which has accumulated a near-duplicate merge-into-state block for each new dependency source (linker-detected, library-build-agent-reported, harness-agent-reported) plus ad hoc apt/brew hint-string assembly for the console message. I want a single, coherent internal representation of \"a library dependency and how to satisfy it\" (bare library name, -l flag, apt package name, brew package name, and which pipeline stage discovered it) that both the deterministic probe and any LLM agent populate into, with one consolidated, de-duplicating merge/persistence point instead of the current scatter of near-identical blocks in cli.py. The goal is to make the dependency-to-install-command pipeline easy to reason about from one place, reduce the stringly-typed source-tag bookkeeping (\"linker\", \"library_agent\", \"harness_agent\") sprinkled across call sites, and prevent future features from needing to add yet another copy-pasted merge block. This is a refactor of existing, working behavior (established most recently by specs/005-harness-system-packages and specs/007-complete-dependency-packaging) — it must not change any user-visible behavior (generated Dockerfile/setup.sh contents, console messages, state.json contents) except for what those two specs already require; it is purely an internal architecture cleanup."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Adding a new dependency-discovery source touches one place, not four (Priority: P1)

A HarnessBuddy contributor wants to add a new way of discovering a required library dependency
(for example, a new static analysis heuristic, or a new kind of agent diagnosis). Today, doing
this correctly requires understanding and modifying `cli.py`'s package-merging logic, adding a
new stringly-typed source tag, and making sure the new source's findings are de-duplicated
against every existing source — a pattern that has already been hand-copied three times. The
contributor wants to add a new source by producing dependency information in one shared shape
and handing it to one merge point, without touching orchestration code that already works for
existing sources.

**Why this priority**: This is the core maintenance cost the consolidation is meant to remove —
every dependency-discovery source added under the current structure increases the chance the
next contributor copies a merge block instead of reusing one, and increases the surface area
for the exact kind of bug this refactor follows from (a source's findings silently discarded or
misfiled — see specs/007-complete-dependency-packaging's motivation).

**Independent Test**: Add a new (test-only) dependency-discovery source that reports one
dependency. Confirm it reaches the generated Dockerfile/setup.sh correctly by producing the
shared dependency representation and calling the single merge point — with no changes to
`cli.py`'s orchestration logic beyond invoking the new source.

**Acceptance Scenarios**:

1. **Given** a new dependency-discovery source that produces the shared dependency
   representation, **When** it reports a dependency already known from another source, **Then**
   the dependency is merged (not duplicated) using the same logic every other source already
   goes through.
2. **Given** the same new source, **When** it is added to the pipeline, **Then** no existing
   merge block in the orchestration code needs to change to accommodate it.

---

### User Story 2 - Tracing why a package did or didn't reach the generated output (Priority: P1)

A HarnessBuddy contributor investigating why a specific system package does or doesn't appear in
a generated `Dockerfile` or `setup.sh` wants to answer that question by reading one module,
rather than cross-referencing symbol-pattern matching, a static translation table, agent
self-report handling, and multiple merge blocks scattered across the orchestration code.

**Why this priority**: This is the direct cause of the debugging time spent resolving the
originating incident (an agent-reported package landing in the wrong platform's install list,
and a blank "missing system libraries" message) — the scatter of logic across four files is what
made that bug hard to isolate in the first place.

**Independent Test**: Given a generated project's `Dockerfile`/`setup.sh` and a package that
does or doesn't appear in it, a contributor unfamiliar with the recent history can identify which
pipeline stage discovered (or failed to discover) that package by reading a single module's
output, without needing to read `cli.py`, `agents.py`, `harness_explorer.py`, and
`package_names.py` together.

**Acceptance Scenarios**:

1. **Given** a generated project, **When** a contributor inspects the persisted dependency
   state, **Then** each package is traceable to exactly one recorded discovering stage, in a
   form that cannot silently typo into a disconnected bucket.

---

### User Story 3 - Refactor introduces zero user-visible behavior change (Priority: P1)

A HarnessBuddy user who already relies on the current dependency-resolution behavior (correct
apt/brew install commands, correct `-l` flags, correct console messages, correct `state.json`
persistence, as established by specs/005 and specs/007) must see no difference in generated
output or CLI behavior as a result of this internal cleanup.

**Why this priority**: This is a refactor of already-correct, already-specified behavior — its
entire value is maintainability, and any regression in existing behavior defeats the purpose and
erodes trust in the generated artifacts (Constitution Principle I).

**Independent Test**: Run the full existing dependency/package-related test suite before and
after the refactor; every test that asserts observable output (generated file contents, console
messages, `state.json` contents) must pass with unmodified assertions.

**Acceptance Scenarios**:

1. **Given** the full existing test suite covering dependency resolution
   (`tests/test_cli.py`, `tests/library_builder/test_agents.py`,
   `tests/library_builder/test_harness_explorer.py`, `tests/library_builder/test_exploration.py`,
   and package-name-mapping tests), **When** the refactor is complete, **Then** every test that
   asserts on generated output or console messages passes without modifying its assertions.
2. **Given** a `state.json` file written by the pre-refactor code, **When** it is loaded by the
   post-refactor code, **Then** it is read and interpreted exactly as before, without requiring
   the user to delete or regenerate it.

---

### Edge Cases

- Two different discovery sources report the same library dependency but with different pieces
  of information known (one has a `-l` flag but no package name yet, another has a package name
  but arrived at it independently) — these must merge into a single dependency record rather
  than becoming two separate, partially-known entries.
- A discovery source's contribution must still be present in the final output even when a later
  stage's own attempt at resolving something else fails (this must not regress from the
  guarantee specs/007-complete-dependency-packaging already established).
- A dependency is only ever partially resolved (e.g., a bare library name with no known `-l`
  flag, apt package, or brew package at all) — this must remain visibly distinct from a fully
  resolved dependency, not silently dropped.
- An existing, already-deployed `.harnessbuddy/<project>/state.json` file uses today's
  free-text source tags (`"linker"`, `"library_agent"`, `"harness_agent"`) — it must continue to
  load correctly after the refactor.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST define a single internal representation of "a library dependency and
  how to satisfy it" — covering the bare library name, its link flag, its apt package name, its
  brew package name, and which pipeline stage discovered it — used by every dependency-discovery
  mechanism instead of each mechanism defining its own separate fields.
- **FR-002**: System MUST provide exactly one function or module responsible for merging newly
  discovered dependencies into persisted project state, de-duplicating across every discovery
  source.
- **FR-003**: Adding a new dependency-discovery source MUST require producing dependency
  information in the shared representation (FR-001) and calling the single merge point (FR-002)
  only — it MUST NOT require adding a new near-duplicate merge block to the orchestration code.
- **FR-004**: System MUST continue to produce identical generated `Dockerfile`, `setup.sh`, and
  console output content, for every scenario already covered by existing tests, before and after
  this refactor.
- **FR-005**: System MUST continue to correctly load and interpret `state.json` files written by
  the pre-refactor code, without requiring the user to delete or regenerate them.
- **FR-006**: The discovering pipeline stage for a given dependency MUST be recorded using a
  closed, enumerated set of sources rather than an arbitrary string, so a misspelled or
  unrecognized tag cannot silently create a disconnected bucket of packages.
- **FR-007**: The shared representation (FR-001) MUST accommodate a dependency that is only
  partially resolved (e.g., a link flag with no known package yet) without discarding the parts
  that are already known.
- **FR-008**: System MUST preserve every guarantee already established by
  specs/005-harness-system-packages and specs/007-complete-dependency-packaging (no duplicate
  packages, no dependency lost when a later stage fails, correct per-platform package names) —
  this refactor changes internal structure only, not those requirements.

### Key Entities

- **Library Dependency**: The consolidated entity from specs/007's data model — a library's bare
  name, its link flag, its apt package name, its brew package name, and its discovering
  pipeline stage — now the single form every discovery mechanism (deterministic probe or LLM
  agent) populates, rather than each mechanism carrying its own parallel fields.
- **Discovery Source**: The pipeline stage that found a given Library Dependency's information,
  drawn from a closed set (e.g. deterministic linker probe, library-build agent, harness-repair
  agent) rather than free text.
- **Dependency Merge Point**: The single place responsible for de-duplicating Library
  Dependencies across every Discovery Source and persisting the result to project state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing dependency/package-related tests that assert on generated output
  (files or console messages) pass without any change to their assertions after the refactor.
- **SC-002**: The number of distinct places in the codebase that write to the persisted
  apt/brew package lists drops from today's count (multiple call sites across the orchestration
  code) to exactly one.
- **SC-003**: A contributor unfamiliar with the dependency-resolution history can identify, by
  reading a single module, where a new dependency-discovery source should report its findings.
- **SC-004**: Zero regressions across the full existing test suite attributable to this
  refactor — the suite's pass/fail outcome for every pre-existing test is unchanged.

## Assumptions

- This is a pure internal refactor prompted by the same merge-block pattern being implemented
  three times while delivering specs/005-harness-system-packages and
  specs/007-complete-dependency-packaging (crossing the "written three times" threshold the
  project's own constitution uses to justify an abstraction) — it introduces no new user-facing
  capability.
- The on-disk `state.json` JSON shape already exists in users' `.harnessbuddy/<project>/`
  directories from prior runs; this refactor may reorganize the code that produces and consumes
  it but must not require a breaking format migration or manual user cleanup.
- specs/005-harness-system-packages and specs/007-complete-dependency-packaging's functional
  requirements remain fully in force and unmodified by this refactor.
- This refactor is scoped to the harness-side dependency pipeline (symbol-pattern matching, the
  static package-name table, agent self-report handling for the harness-repair agent, and the
  orchestration code that merges their findings). The library-build phase's package reporting
  (which has no `-l` flag concept, per specs/007's own assumptions) may be folded into the same
  shared representation only if doing so does not force an unused concept onto that phase.
