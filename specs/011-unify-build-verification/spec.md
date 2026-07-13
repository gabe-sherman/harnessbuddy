# Feature Specification: Unified Build Verification

**Feature Branch**: `011-unify-build-verification`

**Created**: 2026-07-12

**Status**: Draft

**Input**: User description: "Right now the oss-fuzz build environment feels very disjoint. I would
like to use better practices. Right now, only the output of harnessbuddy provides a layout that
resembles a common oss-fuzz build layout, but I instead want this to occur DURING the build and
testing process too. This will let us avoid the current logic in oss_fuzz.py that explicitly runs
compile_harness.sh, etc, and we can instead just build the container and run compile. Furthermore,
the way that harnessbuddy performs build checks should be THE SAME as how the agent can check
builds. Maybe this should be in the form of a script that both harnessbuddy and claude can call
that builds the docker container and runs compile. Essentially I want this build process to be
cleaner, less disjoint, consistent, and follow typical oss-fuzz build patterns."

## Clarifications

### Session 2026-07-12

- Q: Spec 009 validates the library-build and harness-compile stages separately so a failure can be
  pinned to the right stage. Unifying on "build the container, run compile" collapses that into one
  atomic check. How should this tension be resolved? → A: Collapse to one atomic check — both
  HarnessBuddy's own pipeline and the repair agent invoke the same single build-and-verify command;
  stage-level detail (if needed) comes from reading that command's combined log output, not from
  separately gated steps.
- Q: Should the existing compile_commands.json capture (temporary `bear` / CMake-flag
  instrumentation added during verification, then stripped before final generation) change as part
  of this work? → A: No — preserve exactly as it works today. This feature is about converging the
  build/verification orchestration, not revisiting compile_commands.json capture.
- Q: The user's description focuses on the oss-fuzz environment. Does this feature also cover the
  local (non-container) environment? → A: Yes — both environments must converge on the same
  pattern: one verification script per environment, invoked identically by HarnessBuddy's own
  pipeline and by the repair agent.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One verification command per environment, used by everyone (Priority: P1)

A developer maintaining HarnessBuddy needs to know, for a given environment (local or oss-fuzz),
exactly one command that proves a generated build works. Today that isn't true: HarnessBuddy's own
`generate` pipeline validates an oss-fuzz build by building a throwaway "probe" container and
running the library-build and harness-compile steps as separate, hand-rolled commands inside it —
never the actual generated `Dockerfile`/`build.sh` a real user or CI would run. Separately, the
repair agent already has its own script that does the "real" thing (build the actual project
container, run its `compile` entrypoint), but HarnessBuddy's pipeline never calls that script
itself. After this change, both HarnessBuddy's pipeline and the repair agent call the same
verification script for a given environment, so there is exactly one definition of "the build
passed."

**Why this priority**: This is the core of the request — the current three parallel "does the
build work" mechanisms (the pipeline's ad hoc container/host commands, the real generated project,
and the agent's separate script) are the source of the disjointedness. Collapsing them to one is
the change that makes everything else in this feature possible.

**Independent Test**: Can be fully tested by running `generate` against a fixture repository in
each environment and confirming (via logs or an added report field) that the exact same script
path/invocation HarnessBuddy used to gate the pipeline is also the command documented for, and
usable by, the repair agent — and that no other code path independently re-implements "build and
check."

**Acceptance Scenarios**:

1. **Given** a repository being validated in the oss-fuzz environment, **When** `generate` runs its
   build verification, **Then** it does so by invoking the same script the repair agent is told to
   run, and that script's pass/fail result is what gates the pipeline.
2. **Given** a repository being validated in the local environment, **When** `generate` runs its
   build verification, **Then** it does so by invoking the same script the repair agent is told to
   run for the local environment.
3. **Given** a build that fails verification and is handed to the repair agent, **When** the agent
   fixes the build and re-verifies, **Then** it runs the identical script HarnessBuddy itself would
   run, and a successful re-run is accepted as proof the fix works.
4. **Given** a successful `generate` run, **When** a person inspects the run's report or logs,
   **Then** they can see the literal command that was used to confirm success, and can copy/paste
   it to reproduce the result themselves.

---

### User Story 2 - The real oss-fuzz project layout exists throughout the run, not just at the end (Priority: P1)

Today, the recognizable OSS-Fuzz project layout (`project.yaml`, `Dockerfile`, `build.sh`,
`build_library.sh`, `compile_harnesses.sh`, `harness_source/`) only comes into existence as a final
output directory, produced after exploration already finished using a different, synthetic
container setup. A person debugging a run, or an agent repairing a failed build, has to reason
about two different representations of "the project": the ad hoc one used during exploration and
the real one produced afterward. After this change, the working directory used during exploration
already looks like a real OSS-Fuzz project as each piece becomes available, so there is one
representation of the project throughout the run, and the final output is simply that same
directory (or an exact copy of it).

**Why this priority**: Without this, User Story 1's "one verification script" doesn't fully close
the gap — the pipeline would be running the shared script against a different-looking project than
the one it hands to users. This is what the user meant by wanting the OSS-Fuzz-like layout to
exist "during the build and testing process," not just in the final output.

**Independent Test**: Can be fully tested by running `generate` against a fixture repository,
inspecting the working directory partway through a run (or via a post-run diff), and confirming the
directory already contains a `Dockerfile`, `build.sh`, and the per-stage scripts in their
real form before the run completes, rather than a separate probe-image/host-only representation
that gets translated into that shape only at the very end.

**Acceptance Scenarios**:

1. **Given** an oss-fuzz environment run in progress, **When** the library-build stage completes,
   **Then** the working directory already contains a `Dockerfile` and `build_library.sh` consistent
   with what will ship in the final output, not a separate probe-only representation.
2. **Given** an oss-fuzz environment run that an agent is repairing, **When** the agent edits a
   build script, **Then** it edits the same file that both the verification script and the final
   output use — there is no separate "exploration copy" that has to be kept in sync by hand.
3. **Given** a completed, successful run, **When** the final `oss-fuzz/` output is produced,
   **Then** it is the same project directory validated during the run (or an exact copy of it),
   not a re-templated version.

---

### User Story 3 - Failure reports stay useful without separate stage gates (Priority: P2)

Because verification now happens as a single atomic step (build the container, run its build
entrypoint) rather than as separately gated library-build and harness-compile steps, a person or
agent looking at a failure needs enough information in that one combined result to figure out
roughly where things went wrong, without HarnessBuddy having previously enforced a hard stage
boundary.

**Why this priority**: This preserves the diagnostic usefulness that the previous per-stage
approach provided, so collapsing to one atomic check (per the Clarifications) doesn't leave users
and the repair agent with strictly worse failure information than before.

**Independent Test**: Can be fully tested by deliberately breaking a library build and, separately,
a harness compile step in a fixture project, running verification for each, and confirming the
combined output makes it possible to tell which part failed from the log text alone.

**Acceptance Scenarios**:

1. **Given** a library build that fails, **When** the shared verification script runs, **Then** the
   captured output makes clear the failure happened before harness compilation was reached.
2. **Given** a library build that succeeds but harness compilation fails, **When** the shared
   verification script runs, **Then** the captured output makes clear the library build succeeded
   and identifies the harness-compile failure specifically.

---

### Edge Cases

- What happens when Docker itself is unavailable (daemon not running, image pull fails due to
  network)? This must still be distinguishable from a genuine build failure and must not be routed
  to the repair agent, consistent with existing behavior.
- What happens when the harness-compile stage's transitive-dependency discovery needs several
  internal attempts (different link flags/ordering) before the project builds? The internal
  discovery process may still need multiple attempts, but the pass/fail result exposed to the
  pipeline and to users is the shared script's atomic outcome, not the internal attempt count.
- What happens to the compile_commands.json capture instrumentation (temporary `bear` wrapping or
  CMake export flags) that's applied only during verification? It continues to be applied to, and
  stripped from, the build scripts exactly as it works today (see Clarifications) — this feature
  does not change that behavior.
- What happens when an agent-supplied fix changes which system packages are required? The shared
  verification script must be re-run against the updated project layout (updated Dockerfile/build
  scripts), not against stale state from an earlier attempt.
- What happens for a project whose source layout doesn't match the standard `$SRC/<name>` layout
  the generated Dockerfile assumes? The working project layout must account for this the same way
  final generation already does today, so verification and final output don't diverge on this
  point either.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: For a given environment (local or oss-fuzz), there MUST be exactly one verification
  script that determines whether a build passes, used identically by HarnessBuddy's own `generate`
  pipeline and by the repair agent.
- **FR-002**: For the oss-fuzz environment, that verification script MUST build the project's
  actual container image (from its real `Dockerfile`) and confirm success by running the image's
  standard build entrypoint (`compile`), rather than a separately constructed probe image or
  hand-rolled per-stage container commands.
- **FR-003**: For the local environment, that verification script MUST run the project's actual
  build scripts in sequence against its real on-disk layout, the same way it does today, and both
  HarnessBuddy and the repair agent MUST invoke it rather than each independently reimplementing
  the same sequence.
- **FR-004**: During exploration, HarnessBuddy MUST materialize the recognizable OSS-Fuzz project
  layout (`project.yaml`, `Dockerfile`, `build.sh`, `build_library.sh`, `compile_harnesses.sh`,
  `harness_source/`) in the working directory progressively, as each piece becomes available,
  rather than only producing that layout after exploration finishes.
- **FR-005**: The final generated output for a run MUST be the same project directory that was
  validated during the run (or an exact copy of it) — final generation MUST NOT re-derive or
  re-template build artifacts that verification already produced and validated.
- **FR-006**: When a build fails verification and is handed to the repair agent, the agent MUST be
  told to re-verify using the same shared script, and a passing result from that script MUST be
  what HarnessBuddy accepts as proof the fix worked.
- **FR-007**: HarnessBuddy MUST continue to distinguish "the environment itself is unavailable"
  (e.g. Docker not reachable, image pull failure) from a genuine build failure, and MUST NOT route
  environment-unavailable failures to the repair agent.
- **FR-008**: The combined output captured by the shared verification script MUST retain enough
  detail (e.g., which stage's commands ran and their output) that a person or the repair agent can
  determine which part of the build failed, even though verification is now a single atomic
  pass/fail result rather than separately gated stages.
- **FR-009**: Existing compile_commands.json capture behavior (temporary build-instrumentation
  applied during verification, then stripped before final generation) MUST continue to work
  unchanged.
- **FR-010**: A run's report/logs MUST record the literal verification command that was used to
  confirm success or failure, so a person can reproduce it manually.
- **FR-011**: Harness transitive-dependency discovery (determining the correct link flags/library
  ordering for `compile_harnesses.sh`) MAY continue to perform multiple internal attempts, but the
  pipeline-facing and agent-facing pass/fail signal MUST come from the shared verification script,
  not from internal discovery-attempt state.

### Key Entities

- **Verification Script**: The single, environment-specific script (one for local, one for
  oss-fuzz) that both HarnessBuddy's pipeline and the repair agent invoke to determine whether a
  build currently passes. Takes the project's working directory as input and produces a pass/fail
  result plus diagnostic output.
- **Working Project Directory**: The on-disk (and, for oss-fuzz, in-container) representation of
  the OSS-Fuzz-style project (`project.yaml`, `Dockerfile`, `build.sh`, `build_library.sh`,
  `compile_harnesses.sh`, `harness_source/`) that is built up progressively during exploration and
  becomes the final generated output, rather than being reconstructed separately at the end.
- **Verification Result**: The atomic pass/fail outcome of running the Verification Script against
  the Working Project Directory in a given environment, including the captured output needed to
  diagnose a failure and the literal command used to reproduce it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any successful `generate` run, a person can take the exact command HarnessBuddy
  recorded as its verification step, run it themselves against the generated output, and get the
  same passing result with no additional fixes, 100% of the time.
- **SC-002**: The codebase has exactly one place per environment that defines "how to check whether
  a build passes" — down from the three separate mechanisms (ad hoc pipeline commands, the final
  generated layout, and the agent's own script) that exist today.
- **SC-003**: When the repair agent is invoked, it verifies its own fix using the identical command
  HarnessBuddy itself uses to gate the pipeline, with no separate host-only or pipeline-only
  recheck logic remaining.
- **SC-004**: A person debugging a failed or in-progress run can inspect the working directory at
  any point after the library-build stage and find a real, runnable OSS-Fuzz project layout, not a
  representation that only becomes real after the run finishes.
- **SC-005**: Existing behavior that this feature does not intend to change — compile_commands.json
  capture, reuse of agent-fixed scripts in final output, and Docker-unavailability handling —
  continues to work with no observed regressions.

## Assumptions

- The verification build that captures compile_commands.json (via temporary `bear`/CMake-flag
  instrumentation) is the same build used for the atomic pass/fail check; the final shipped
  scripts have that instrumentation stripped without a separate re-validation build, consistent
  with how this already works today.
- Collapsing library-build and harness-compile verification into one atomic per-environment check
  means stage-specific pass/fail gating (e.g. "stop before attempting harness compile if the
  library build failed") is no longer a hard pipeline boundary; it becomes a matter of the shared
  script's own internal ordering (e.g. `build_library.sh` still runs before
  `compile_harnesses.sh` inside `build.sh`) and of making combined output easy to read, not of
  HarnessBuddy separately gating each stage.
- The repair agent continues to edit files on the host filesystem for both environments; only the
  verification mechanism changes to route through the shared script.
- Custom environments beyond local and oss-fuzz remain out of scope, consistent with prior work in
  this area.
- Existing flags such as `--skip-validation` continue to mean "don't let a failed verification
  result stop the pipeline"; the exact interaction is a planning-level detail, not a scope
  decision for this specification.
