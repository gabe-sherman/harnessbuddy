# Feature Specification: Harness Linker Dependencies Become Install Commands

**Feature Branch**: `005-harness-system-packages`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "When a harness compilation requires extra system package -l flags (like -lzstd, -lz, etc.) I want to make sure that the Dockerfile for the OSS-Fuzz build and the setup.sh for local build have the appropriate apt install (or brew) commands."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Install commands cover every linked dependency, not just the ones missing locally (Priority: P1)

A HarnessBuddy user generates an OSS-Fuzz project for a library whose harness needs to link against extra system libraries (e.g. zstd, lz, lzma). On the machine where HarnessBuddy explored the harness build, those libraries already happen to be installed, so the exploration succeeds without ever reporting them as missing. Today that means the generated Dockerfile and setup.sh never mention those packages at all — the harness link step then fails the first time it runs somewhere that doesn't already have them (a fresh OSS-Fuzz Docker build, a teammate's machine, CI).

The user wants the generated Dockerfile and setup.sh to declare every system package the harness link step actually depends on, regardless of whether the exploration machine happened to already have it.

**Why this priority**: This is the core gap — without it, generated projects silently fail the first time they're built somewhere other than the exact machine HarnessBuddy ran on, which defeats the purpose of producing a portable OSS-Fuzz project and local scaffold.

**Independent Test**: Run HarnessBuddy against a library whose harness resolves to extra link flags (e.g. `-lzstd -lz -llzma`) on a machine that already has those libraries installed. Confirm the generated Dockerfile and setup.sh each contain install commands for the corresponding packages, then confirm the harness link step succeeds when run in a clean environment without those packages pre-installed.

**Acceptance Scenarios**:

1. **Given** a harness whose compilation succeeds locally only because the exploration machine already has the required libraries installed, **When** HarnessBuddy generates the OSS-Fuzz project, **Then** the Dockerfile includes an apt install step for every package that maps to a linked library.
2. **Given** the same harness, **When** HarnessBuddy generates the local dev scaffold, **Then** setup.sh includes an apt install step (Linux) or brew install step (macOS) for the same packages.
3. **Given** a generated Dockerfile and setup.sh produced under this behavior, **When** the harness is built in a clean environment with no pre-installed system libraries beyond the ones declared, **Then** the harness link step succeeds without manual intervention.

---

### User Story 2 - Unmapped dependencies are surfaced, never silently dropped (Priority: P2)

A required linked library sometimes has no known corresponding package name in HarnessBuddy's mapping. The user wants to be told this clearly instead of ending up with a Dockerfile/setup.sh that quietly omits the dependency and fails later with a confusing linker error.

**Why this priority**: Prevents a partially-solved dependency from looking indistinguishable from a fully-solved one, which would erode trust in the generated output.

**Independent Test**: Run HarnessBuddy against a harness that resolves a link flag with no entry in the package mapping. Confirm the tool's output calls out the unmapped dependency by name, and the generated files make clear that dependency was not automatically installed.

**Acceptance Scenarios**:

1. **Given** a harness link step depends on a library with no known package mapping, **When** outputs are generated, **Then** the user-facing output (console message and/or generated file comment) names the unmapped library explicitly.

---

### User Story 3 - Packages accumulate across pipeline stages without duplicates (Priority: P3)

Packages needed by the library build itself (e.g. autotools build dependencies) and packages needed only by the harness link step both need to end up in the same install commands, without the same package being listed twice or one stage's findings overwriting another's.

**Why this priority**: Correctness/hygiene concern once P1 and P2 are in place — avoids noisy or contradictory generated scripts.

**Independent Test**: Run HarnessBuddy against a library where both the library build phase and the harness link phase each contribute required packages, including one package needed by both. Confirm the generated install commands list every required package exactly once.

**Acceptance Scenarios**:

1. **Given** the library build phase and the harness link phase both report a need for the same package, **When** outputs are generated, **Then** that package appears exactly once in each generated install command.
2. **Given** the library build phase reports package A and the harness link phase reports package B, **When** outputs are generated, **Then** both A and B appear in the generated install commands.

---

### Edge Cases

- A linked library maps to a different package name on apt vs. brew — the correct name must be used in each of the two output files.
- The harness link step fails on the exploration machine because a package is genuinely absent there too (already-supported case) — behavior for this case must remain correct alongside the new "succeeded despite being unmapped in outputs" case.
- A linked library is a base-system library (e.g. libc, libm, pthread) that ships with every build environment and must continue to be excluded from install commands rather than treated as a missing package.
- The same library dependency is discovered on both macOS (via brew-prefix probing) and Linux-style linker errors within the same run.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST determine the complete set of external library dependencies the generated harness link command relies on, independent of whether the machine used to explore the harness build already had those libraries installed.
- **FR-002**: System MUST translate each external library dependency identified in FR-001 into its corresponding installable package name(s) for Debian/Ubuntu (apt) and, where applicable, Homebrew (brew).
- **FR-003**: The generated OSS-Fuzz Dockerfile MUST include an apt install step listing every package resolved from the harness's linked dependencies, in addition to any packages already required by the library build itself.
- **FR-004**: The generated local setup.sh MUST include an apt install step (Linux) or brew install step (macOS) listing the same set of packages as FR-003, matching the existing platform-specific behavior of the local scaffold.
- **FR-005**: System MUST NOT list a package more than once within a single generated install command, even when the same underlying dependency is discovered by more than one pipeline stage (library build vs. harness link).
- **FR-006**: System MUST NOT emit install commands for dependencies that are satisfied by the base build environment itself (e.g. the C standard library, pthread, dl, math library) rather than by an installable package.
- **FR-007**: When a linked dependency has no known package mapping, system MUST surface that dependency to the user by name (via console output, generated file comment, or both) rather than omitting it without explanation.
- **FR-008**: Packages already known from earlier pipeline stages (library build phase, prior agent-repaired builds) MUST be preserved and merged with harness-derived packages rather than replaced.

### Key Entities

- **Linked Dependency**: An external library the compiled harness needs at link time (e.g. `zstd`, `z`, `lzma`), identified independent of whether it was missing on the exploration machine.
- **Package Mapping**: The association between a Linked Dependency and its installable package name(s) for apt and brew.
- **Generated Install Step**: The apt/brew install command written into the Dockerfile or setup.sh, covering the full, de-duplicated set of packages needed across all pipeline stages.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For libraries whose harness links against extra system libraries, 100% of those libraries have a corresponding install command in both the generated Dockerfile and setup.sh, whenever a package mapping exists.
- **SC-002**: A generated OSS-Fuzz project builds its harness successfully in a clean environment (no pre-installed extra libraries beyond what the generated Dockerfile installs) on the first attempt, for any dependency with a known package mapping.
- **SC-003**: Every linked dependency without a known package mapping is visibly reported to the user, with zero cases of a dependency being dropped without any indication.
- **SC-004**: No generated install command contains a duplicate package entry.

## Assumptions

- The existing dependency-name-to-package-name mapping approach (covering common libraries such as zstd, zlib, lzma, openssl, etc.) is extended by this feature rather than replaced; growing that mapping's coverage over time is out of scope for this feature.
- OSS-Fuzz Dockerfiles always target a Debian/Ubuntu-based image, so only apt install commands are relevant there; brew is only relevant to the local setup.sh on macOS.
- "Succeeded despite the exploration machine already having the package" and "failed because the exploration machine was missing the package" are both valid ways of discovering a required dependency, and both must result in the same generated install commands.
- Base system libraries that ship with any standard build toolchain (libc, libm, pthread, dl, etc.) are out of scope for install commands and continue to be excluded.
