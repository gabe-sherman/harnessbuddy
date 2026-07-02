# Feature Specification: Complete Library Dependency Packaging

**Feature Branch**: `007-complete-dependency-packaging`

**Created**: 2026-07-02

**Status**: Implemented

**Input**: User description: "I want to better improve the library dependency infrastructure for harnessbuddy. I see library dependencies spanning two core roles: 1) Installing required system packages. This should essentially provide the relevant system packages to install via apt for the Dockerfile, and the same for either apt (on linux) or brew (on mac) in the local setup.sh. Even in cases where a system package did not need to be installed before providing a -l flag, it should still be included in this for portability to other environments. 2) Resolving -l flags during the compilation process. The goal of both the deterministic and agentic stage of this is to find the correct -l flag to include, and if that fails, define the required system package. Uninstalled system packages should be brought up to the user, but even installed system packages that the library depends on should make their way into Dockerfile and setup.sh setup"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every linked dependency reaches the generated install commands, not only the ones that were missing (Priority: P1)

A HarnessBuddy user generates an OSS-Fuzz project for a library whose harness needs to link against a system library. Sometimes that library is already present on whichever machine (or agent) resolved the correct `-l` flag, so nothing was ever "missing" from that machine's point of view. Today, a dependency resolved this way by an agent's diagnosis can end up encoded only as a linker flag in the harness script, with no corresponding install command in the generated Dockerfile or setup.sh — the harness link step then fails the first time it runs somewhere that doesn't already have that library (a fresh OSS-Fuzz Docker build, a teammate's machine, CI).

The user wants every library the generated harness link command actually depends on to have a corresponding install command in both the Dockerfile and setup.sh, regardless of whether resolving it ever required installing something new on the machine that discovered it.

**Why this priority**: This is the core portability gap — without it, a generated project can build successfully on the machine that produced it while silently failing everywhere else, which defeats the purpose of producing a portable OSS-Fuzz project and local scaffold.

**Independent Test**: Run HarnessBuddy against a library where the harness-repair agent resolves a missing symbol by adding a `-l` flag for a library that happens to already be installed on the agent's machine. Confirm the generated Dockerfile and setup.sh each contain an install command for the corresponding package, then confirm the harness link step succeeds when run in a clean environment without that package pre-installed.

**Acceptance Scenarios**:

1. **Given** an agent resolves a harness link failure by adding a new `-l` flag for a library already present on its machine, **When** HarnessBuddy generates the OSS-Fuzz project, **Then** the Dockerfile includes an apt install step for the package providing that library.
2. **Given** the same resolution, **When** HarnessBuddy generates the local dev scaffold, **Then** setup.sh includes an apt install step (Linux) or brew install step (macOS) for the same package.
3. **Given** a generated Dockerfile and setup.sh produced under this behavior, **When** the harness is built in a clean environment with no pre-installed system libraries beyond the ones declared, **Then** the harness link step succeeds without manual intervention.

---

### User Story 2 - Deterministic and agentic resolution both prioritize finding the correct `-l` flag, and fall back to naming a package only when that fails (Priority: P1)

When a harness link step fails on an unresolved symbol, the system should first try to determine which `-l` flag resolves it — whether through deterministic symbol-pattern matching or an agent's own diagnosis. Only when a required library genuinely cannot be found anywhere on the resolving machine should the system fall back to telling the user which system package to install.

**Why this priority**: This is the mechanism that makes User Story 1 possible — a `-l` flag has to actually be identified and encoded into the harness build script before there is anything to record a package for. It also keeps the primary path (finding the flag) distinct from the fallback path (asking the user to install something), so the two don't get conflated.

**Independent Test**: Run HarnessBuddy against a harness link failure that an agent can resolve entirely by adding a `-l` flag (library already available). Confirm no "please install a package" message is shown to the user, but the resulting harness build script and the generated install commands both reflect the resolved dependency. Separately, run against a failure where the library genuinely isn't available anywhere; confirm the user is told which package(s) to install.

**Acceptance Scenarios**:

1. **Given** a harness link failure where the correct `-l` flag can be determined and the library is available, **When** resolution completes, **Then** the harness build script includes the flag and the user is not asked to install anything.
2. **Given** a harness link failure where the required library is not available anywhere the resolving process can check, **When** resolution completes, **Then** the user is shown which specific package(s) must be installed before the harness build can succeed.

---

### User Story 3 - Package names are correct for the platform they target, not guessed from a single source (Priority: P2)

The apt package name and the brew package name for the same library are frequently different from each other and from the library's own name (for example, one real HarnessBuddy run needed the brew formula `openldap` on macOS but the apt package `libldap2-dev` on Debian/Ubuntu). The user wants both names determined correctly and independently, rather than one name being reused for both platforms or an apt-only name leaking into a brew install command.

**Why this priority**: A wrong package name in a generated Dockerfile or setup.sh causes the install step itself to fail, which is worse than surfacing nothing — the user sees a confusing package-manager error instead of a clear "install X" message.

**Independent Test**: Run HarnessBuddy against a library dependency whose apt and brew package names differ. Confirm the generated Dockerfile uses the correct apt name and the generated setup.sh uses the correct brew name on macOS.

**Acceptance Scenarios**:

1. **Given** a library dependency with different apt and brew package names, **When** outputs are generated, **Then** the Dockerfile's apt install command uses the apt name and setup.sh's brew install command (on macOS) uses the brew name.

---

### User Story 4 - Dependencies from every stage accumulate without duplication or loss (Priority: P3)

Packages needed by the library build itself, packages the deterministic harness probe resolves, and packages an agent resolves or reports must all end up in the same install commands — without the same package appearing twice, and without one stage's findings being discarded when a later stage runs (including when a later stage's own attempt does not fully succeed).

**Why this priority**: Correctness/hygiene concern once Stories 1-3 are in place — avoids noisy, contradictory, or incomplete generated scripts, and closes the specific failure mode where a later stage overwrites an earlier stage's already-correct findings with nothing.

**Independent Test**: Run HarnessBuddy against a library where the library-build phase, the deterministic harness probe, and an agent invocation each contribute at least one required package, including one package needed by more than one stage. Confirm the generated install commands list every required package exactly once, and confirm none of the earlier stages' findings disappear if a later stage's own attempt fails.

**Acceptance Scenarios**:

1. **Given** two different pipeline stages each report a need for the same package, **When** outputs are generated, **Then** that package appears exactly once in each generated install command.
2. **Given** an earlier stage has already identified a required package, **When** a later stage runs and does not itself succeed, **Then** the earlier stage's package is still present in the generated install commands.

---

### Edge Cases

- A `-l` flag is resolved by an agent using a library the resolving machine already had — the corresponding package must still be recorded for portability (the core gap this feature closes), not just when the machine was missing it.
- The same library dependency is discovered independently by both the deterministic probe and an agent within the same run — it must appear once in the generated output, not twice.
- An agent's diagnosis requires no new packages at all (the failure was resolved purely by reordering static libraries or adjusting search paths) — no phantom package entries should be added.
- A library dependency cannot be resolved to any package name with confidence, on either platform — this must be surfaced to the user distinctly from a successfully-resolved dependency, never silently dropped.
- A dependency needed only during the library's own build (not at harness link time) has no `-l` flag to resolve — it is still expected to reach the install commands, just through the library-build phase's existing reporting rather than through `-l` flag resolution.
- A library dependency is a base-system library (e.g. libc, libm, pthread) that ships with every build environment — it must continue to be excluded from install commands rather than treated as a package to install.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST record every library dependency a generated harness link command relies on, independent of whether resolving that dependency required installing a new system package on the machine that discovered it.
- **FR-002**: For each recorded library dependency, system MUST determine both the apt package name (Debian/Ubuntu) and, where applicable, the brew package name (macOS) that provides it, determining each independently rather than assuming one name applies to both platforms.
- **FR-003**: Both deterministic build-time exploration and any agent-based diagnosis MUST attempt to identify the correct `-l` linker flag for an unresolved symbol before falling back to declaring a system package requirement.
- **FR-004**: Whenever a `-l` flag is newly added to the harness link command by either deterministic exploration or agent diagnosis, the corresponding package name(s) for that library MUST be recorded for install-command generation, even when resolving the flag did not require installing anything new.
- **FR-005**: When a required library cannot be resolved without installing a system package, system MUST clearly surface to the user which specific package(s) — by platform — need installing before the harness build can succeed.
- **FR-006**: The generated OSS-Fuzz Dockerfile MUST include an apt install step listing every recorded library dependency (library-build phase and harness link phase combined), independent of whether that dependency ever caused a build failure.
- **FR-007**: The generated local setup.sh MUST include an apt install step (Linux) or brew install step (macOS) listing the same set of packages as FR-006, matching the existing platform-specific behavior of the local scaffold.
- **FR-008**: System MUST NOT list the same package more than once within a single generated install command, regardless of how many pipeline stages (library build, deterministic harness probe, harness agent) contributed it.
- **FR-009**: A package already recorded by an earlier pipeline stage MUST NOT be discarded or overwritten when a later stage runs, whether or not that later stage's own attempt succeeds.
- **FR-010**: System MUST NOT emit install commands for dependencies satisfied by the base build/toolchain environment itself (e.g. the C standard library, pthread, dl, math library) rather than by an installable package.
- **FR-011**: When a library dependency has no known package mapping on a given platform, system MUST surface that dependency to the user by name rather than omitting it without explanation.

### Key Entities

- **Library Dependency**: An external library a harness link command or the library's own build relies on, identified by its bare name (the part passed to `-l`, where applicable), independent of whether resolving it required installing something new.
- **Package Resolution**: The apt package name and, where applicable, the brew package name that provide a given Library Dependency — determined independently per platform, whether from a known mapping or from an agent's own diagnosis.
- **Generated Install Step**: The de-duplicated apt or brew install command written into the Dockerfile or setup.sh, covering every recorded Library Dependency across all contributing pipeline stages.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of libraries a generated harness link command depends on have a corresponding install command in both the Dockerfile and setup.sh whenever a package name is known, regardless of whether that dependency ever caused a build failure.
- **SC-002**: A generated OSS-Fuzz project's harness link step succeeds on the first attempt in a clean environment that has only the packages listed in the generated Dockerfile installed, including for dependencies an agent resolved without itself needing a new install.
- **SC-003**: Zero cases where a required system package is silently omitted from generated output without being surfaced to the user first.
- **SC-004**: No generated install command contains a duplicate package entry, across any combination of contributing pipeline stages.
- **SC-005**: Zero cases where an apt package name appears in a brew install command, or vice versa.

## Assumptions

- This feature extends `specs/005-harness-system-packages` (which covers the deterministic-probe path) rather than replacing it; the existing bare-library-to-package mapping remains in place for libraries it already recognizes.
- An LLM agent, when it identifies a library dependency, is expected to know or reason out the correct apt and brew package names itself, rather than requiring every possible library to be pre-catalogued in a static mapping table maintained by HarnessBuddy.
- "Resolved a `-l` flag without needing to install a new package" and "resolved a `-l` flag by identifying a package that must be installed" are both valid discovery paths, and both must result in the same generated install-command coverage.
- The library-build phase (compiling the library itself, before any harness link step exists) does not have a `-l` flag to resolve; its own package needs are reported and recorded independently of harness link resolution.
- OSS-Fuzz Dockerfiles always target a Debian/Ubuntu-based image, so only apt install commands are relevant there; brew is only relevant to the local setup.sh on macOS.
- Base system libraries that ship with any standard build toolchain (libc, libm, pthread, dl, etc.) are out of scope for install commands and continue to be excluded.
