# Feature Specification: Structured Build Environments

**Feature Branch**: `009-structured-build-environments`

**Created**: 2026-07-10

**Status**: Draft

**Input**: User description: "I want to take a more structured approach to library building. As of now, all
building and testing occurs in the local environment. But this does not account fully for tests in containers,
etc. So I would actually like the user to choose the environment in which testing occurs (locally, oss-fuzz, or
maybe even custom). Essentially take porting builds from the current system to the target environment from a
post-build process to a DURING-build process where steps can be validated along the way. Additionally, agents
should be able to take advantage of the scripts in ./agents/scripts to verify builds, but these scripts need to
be updated too"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Choose a target environment for a run (Priority: P1)

A user running `harnessbuddy generate` wants the library build and harness compilation to be validated against
the environment they actually care about — either their local machine or an OSS-Fuzz-equivalent container —
instead of always validating on the host and hoping the translated OSS-Fuzz output happens to also work.

**Why this priority**: This is the foundational capability the rest of the feature depends on. Without an
explicit environment choice, there is no way to change what "success" means for a run.

**Independent Test**: Can be fully tested by running `generate` once with the local environment selected and
once with the oss-fuzz environment selected against the same repository, and observing that each run validates
the build against its selected environment and reports which environment was used.

**Acceptance Scenarios**:

1. **Given** a repository with a supported build system, **When** the user runs `generate` and selects the
   local environment, **Then** the library build and harness compilation are executed and validated on the
   local host, and the final report states the local environment was used.
2. **Given** the same repository, **When** the user runs `generate` and selects the oss-fuzz environment,
   **Then** the library build and harness compilation are executed and validated inside an OSS-Fuzz-equivalent
   container, and the final report states the oss-fuzz environment was used.
3. **Given** a user runs `generate` without specifying an environment, **When** the run proceeds, **Then**
   HarnessBuddy defaults to the local environment, preserving today's behavior.

---

### User Story 2 - Validate each stage as it happens, in the target environment (Priority: P1)

A user wants to know, at the moment a build stage runs, whether it actually works in the environment they
selected — rather than discovering after the fact that host-only validation didn't carry over to OSS-Fuzz.

**Why this priority**: This is the core structural change requested: moving environment validation from a
post-build translation step to an in-line, per-stage gate. It directly addresses the gap where a host build
succeeding does not guarantee the generated OSS-Fuzz project will also build.

**Independent Test**: Can be fully tested by intentionally breaking a step that only fails in one environment
(e.g. a container-only missing system package) and confirming the pipeline reports the failure at that specific
stage, in that specific environment, before any later stage runs.

**Acceptance Scenarios**:

1. **Given** the oss-fuzz environment is selected, **When** the library build stage completes, **Then** it is
   validated inside the OSS-Fuzz-equivalent container before the harness compilation stage begins.
2. **Given** the oss-fuzz environment is selected, **When** the harness compilation stage completes, **Then**
   it is validated inside the OSS-Fuzz-equivalent container before final project generation.
3. **Given** a stage fails validation in the selected environment, **When** the pipeline detects the failure,
   **Then** it stops before running later stages and reports which stage failed, in which environment, with the
   relevant failure output.
4. **Given** a stage passes validation in the selected environment, **When** final output scaffolding is
   generated, **Then** the generated local/ and oss-fuzz/ output reflects the results actually observed during
   that stage's validation, rather than a result inferred solely from a host-only run.

---

### User Story 3 - Agent repair verifies fixes in the target environment (Priority: P2)

When a build or harness-compile stage fails validation, the LLM repair agent should confirm its fix actually
works in the environment the user selected, using the verification scripts under `agents/scripts/`, rather than
only confirming the fix compiles on the host.

**Why this priority**: Without this, structured per-environment validation would stop at the deterministic
pipeline and agent-assisted repairs could still silently regress to host-only validation, undermining the goal
of the feature.

**Independent Test**: Can be fully tested by forcing a build failure that only reproduces in the oss-fuzz
environment, invoking agent fallback, and confirming the agent's fix is checked with the environment-appropriate
verification script before the agent reports success.

**Acceptance Scenarios**:

1. **Given** the local environment is selected and a build stage fails, **When** the repair agent proposes a
   fix, **Then** the agent verifies the fix using the local-environment verification script before reporting
   success.
2. **Given** the oss-fuzz environment is selected and a build stage fails, **When** the repair agent proposes a
   fix, **Then** the agent verifies the fix using the container-based verification script before reporting
   success.
3. **Given** a verification script referenced by an agent is broken or does not match the actual generated
   script names/interfaces, **When** an agent attempts to use it, **Then** the mismatch is treated as a defect
   to fix as part of this feature, not a condition the agent must work around.

---

### Edge Cases

- What happens when the oss-fuzz environment is selected but Docker (or another required container tool) is
  unavailable? The run must fail with an actionable message identifying the missing dependency, without
  attempting agent fallback.
- What happens when a stage passes in the local environment but the user later re-runs the same repository with
  the oss-fuzz environment selected? Each run's stage validation is independent and reflects only the selected
  environment for that run; no result is carried over from a prior run in a different environment.
- What happens when network access needed to build the container image is unavailable? This is reported as an
  environment-availability failure, distinct from a build-logic failure, and does not trigger agent fallback.
- How does the system handle a repository whose build succeeds locally but fails inside the oss-fuzz container
  at a step host validation cannot observe (e.g. a container-only missing system library)? The failure must be
  attributed to the stage and environment where it actually occurred, with output distinguishing it from a
  host-reproducible failure.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST let the user select a target build/test environment for a `generate` run from: local
  (the current host-based flow) or oss-fuzz (an OSS-Fuzz-equivalent container flow).
- **FR-002**: System MUST default to the local environment when the user does not explicitly select one,
  preserving current behavior for existing users.
- **FR-003**: System MUST execute and validate the library build stage inside the selected environment before
  proceeding to the harness compilation stage, for both supported environments.
- **FR-004**: System MUST execute and validate the harness compilation stage inside the selected environment
  before proceeding to final output generation.
- **FR-005**: System MUST stop the pipeline as soon as a stage fails validation in the selected environment, and
  MUST NOT run later stages on unvalidated results.
- **FR-006**: System MUST report, for each stage, which environment it was validated in and whether validation
  passed or failed, as part of the run's final report and run statistics.
- **FR-007**: When a stage fails, the failure report MUST include environment-specific diagnostic output (e.g.
  container build/run logs for oss-fuzz, process output for local) sufficient to diagnose the failure without
  re-running the pipeline.
- **FR-008**: Final generated output (the `local/` scaffold and the `oss-fuzz/` project) MUST reflect the
  results actually observed while validating stages in the selected environment, rather than being derived
  solely from a host-only exploration that is translated afterward.
- **FR-009**: When a stage fails and an LLM repair agent is enabled, the agent MUST make its fix on the host
  filesystem (as it does today) and then verify that fix by invoking an environment-appropriate verification
  script before reporting success, matching the environment selected for the run.
- **FR-010**: The verification scripts under `agents/scripts/` (`check_local_build.sh`, `check_docker_build.sh`)
  MUST be corrected to match the actual generated script names, arguments, and interfaces they are meant to
  verify, so agents can invoke them successfully.
- **FR-011**: System MUST continue to support running with agent fallback disabled (`--no-agents`), regardless
  of which environment is selected.
- **FR-012**: When the oss-fuzz environment is selected and the required container tooling (e.g. Docker) or
  network access is unavailable, the system MUST fail with an actionable, environment-specific message and MUST
  NOT invoke agent fallback for that failure.
- **FR-013**: Custom, user-defined build/test environments are out of scope for this feature. The environment
  selector is not required to reserve a "custom" option.

### Key Entities

- **Target Environment**: The place a build/harness stage is executed and validated for a given run. Has an
  identifier (local or oss-fuzz), the tooling it depends on (host toolchain vs. container runtime), and the
  verification script used to confirm a stage passed in it.
- **Build Stage**: A discrete, ordered step in the pipeline (library build, harness compilation) that now carries
  its own per-environment validation outcome instead of a single host-only result translated at the end.
- **Stage Validation Result**: The pass/fail outcome of validating one build stage inside one target environment,
  including diagnostic output, used both for pipeline gating and for the final run report.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can select either the local or oss-fuzz environment for a `generate` run and see which
  environment was used to validate each stage in the final report, with no additional manual steps.
- **SC-002**: When a build or harness-compile step only fails in the oss-fuzz environment, that failure is
  surfaced at the moment the corresponding stage runs — not discovered later by manually building the generated
  OSS-Fuzz project by hand.
- **SC-003**: 100% of stage failures reported to the user identify the specific stage and environment in which
  they occurred, with diagnostic output attached.
- **SC-004**: The agent repair scripts under `agents/scripts/` successfully verify a fix in the selected
  environment without a human needing to hand-edit the scripts first, for both supported environments.
- **SC-005**: Generated OSS-Fuzz project output matches what was actually validated during the run at least as
  often as it does today, with no regression in local-environment run behavior for users who do not opt into
  the oss-fuzz environment.

## Assumptions

- Custom environments are explicitly out of scope for this feature (confirmed with the requester); the local and
  oss-fuzz environments are the only selectable targets.
- The oss-fuzz environment reuses the existing OSS-Fuzz base-builder container approach already referenced by
  `agents/scripts/check_docker_build.sh`, rather than introducing a new container strategy.
- Selecting the oss-fuzz environment means both the library build stage and the harness compilation stage are
  validated inside the container as they happen, matching the request to move validation from a post-build
  translation step to a during-build, per-stage gate.
- The repair agent continues to edit files on the host filesystem for both environments; only the verification
  step changes to match the selected environment (confirmed with the requester).
- Existing flags such as `--skip-validation` are expected to interact with environment selection (e.g. skipping
  the target-environment gate entirely) but the exact flag surface is a planning-level detail, not a scope
  decision for this specification.
- Run statistics (`stats.json`) are expected to gain per-stage, per-environment validation results, consistent
  with how they already track agent phase outcomes today.
