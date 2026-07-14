# Feature Specification: Clear Build Logging and Diagnostics

**Feature Branch**: `012-clear-build-logging`

**Created**: 2026-07-14

**Status**: Draft

**Input**: User description: "I want to make the logging and output of harnessbuddy more clear for the user. I think sometimes streaming the whole build output can be a bit overwhelming. My goals are 1) At any given time, the user should know what phase harnessbuddy library build is currently in (static library build, agentic library build, ...). Furthermore, when a failure occurs the user should have a clear diagnostic or place in the code they can point to. Furthermore, turning on debugging can offer additional context into failures, etc."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Always know the current phase (Priority: P1)

While `harnessbuddy generate` is running against a repository, a user watching
the terminal can tell, at any moment, which stage of the pipeline is currently
executing — for example repo ingestion, static analysis, static library build,
agent-assisted library repair, harness compile probe, agent-assisted harness
repair, or output generation — without having to read or interpret the raw
build tool output scrolling past.

**Why this priority**: This is the core complaint driving the feature: today
a user watching raw, streamed build output has no reliable way to tell what
HarnessBuddy is doing right now, or whether it's making progress versus
stuck. This is the highest-value, most visible change and is a prerequisite
for the failure-diagnostic story (a diagnostic is far less useful if the user
doesn't already know which phase it came from).

**Independent Test**: Run `harnessbuddy generate` against a repository that
completes successfully. Confirm that, by scanning the console output for
phase start/end announcements alone — without needing to read or parse the
underlying build tool's own output that streams between them — an observer
can correctly name each phase the run passed through and roughly when it
started and ended.

**Acceptance Scenarios**:

1. **Given** a `generate` run has just started, **When** it begins repository
   ingestion, **Then** the console clearly indicates "repository ingestion"
   (or equivalent) is the active phase before any ingestion activity is shown.
2. **Given** a deterministic library build has failed and HarnessBuddy is
   about to fall back to the LLM repair agent, **When** the agent-assisted
   attempt begins, **Then** the console clearly distinguishes this phase from
   the preceding static build phase — even with each phase's full raw output
   streaming in between — so the user should never mistake agent-driven
   output for a plain deterministic build or vice versa.
3. **Given** a long-running phase (e.g. a large library build) is still in
   progress, **When** several seconds or minutes pass, **Then** by default the
   phase's own streaming raw output continues to demonstrate the run is alive
   and which phase it is in; a user who has explicitly opted into `--quiet`
   accepts reduced output, including possible stretches of silence during a
   long phase, as the known trade-off of that choice rather than a defect.
4. **Given** a full `generate` run completes successfully, **When** the user
   reviews the full console transcript afterward, **Then** they can identify
   the complete, ordered list of phases the run went through, whether or not
   `--quiet` was used.

---

### User Story 2 - Get a clear diagnostic when something fails (Priority: P2)

When any phase of a `generate` run fails, the user is shown a concise,
readable summary that identifies which phase failed, what specifically went
wrong, and where to look for more detail — instead of being left to scroll
back through a wall of streamed build output to figure out what happened and
where.

**Why this priority**: Failure is the moment a user most needs clarity, and
today's raw-output dump is the specific pain point called out for fixing. It
depends on Story 1 (phase identity) to be meaningful, so it is second
priority, but delivers the bulk of the requested value once phase visibility
exists.

**Independent Test**: Run `harnessbuddy generate` against a repository
crafted to fail at a specific, known phase (e.g. an unsupported build system,
or a library whose build script is broken). Confirm the final console output
names the failed phase and the specific failing step, and points to a
location (e.g. a saved log) where the full underlying output can be read,
without requiring the user to enable any special option first.

**Acceptance Scenarios**:

1. **Given** the static library build fails and no repair agent is invoked,
   **When** the run ends, **Then** the console shows which phase failed
   (static library build), a short description of the failure, and a
   reference to where the complete raw output was preserved.
2. **Given** an agent-assisted repair attempt itself fails (the LLM could not
   produce a working build), **When** the run ends, **Then** the diagnostic
   distinguishes "agent repair failed" from "static build failed" so the user
   understands a repair was attempted and did not succeed, rather than
   assuming no repair was tried.
3. **Given** a failure occurs in an early, non-build phase (e.g. the
   repository could not be cloned, or the build system could not be
   detected), **When** the run ends, **Then** the diagnostic still correctly
   names that phase rather than defaulting to a generic or misleading label.
4. **Given** two phases fail in sequence during one run (e.g. static build
   fails, then the subsequent repair attempt also fails), **When** the user
   reads the final diagnostic, **Then** they can tell which phase failed
   first and which failed last, in order.

---

### User Story 3 - Opt into deeper diagnostic detail (Priority: P3)

A user who needs to investigate a failure more deeply than the default
diagnostic allows can turn on a debug option that reveals additional context
— the full raw output of the step that failed inlined directly with its
diagnostic (so it doesn't have to be found by scrolling back or opening the
log file), and any other internal implementation-level detail useful for
troubleshooting — without that extra detail being shown during normal,
successful runs.

**Why this priority**: This is explicitly requested but is an enhancement on
top of Stories 1 and 2. Since the default view already streams full raw
output (Story 1), most failures are diagnosable from the default output plus
the Story 2 diagnostic; debug mode's remaining value is convenience (the
failing step's output right next to its diagnostic instead of scrolled past)
and surfacing internal detail (e.g. Python-level debug logging) that never
streams at all otherwise. It is most valuable once phases and diagnostics
are already labeled clearly, which is why it is sequenced last.

**Independent Test**: Run `harnessbuddy generate` twice against the same
failing repository — once with the debug option off and once with it on.
Confirm both runs show the same phase banners and Story 2 diagnostic; the
debug run additionally shows the extra internal context (the failing step's
full raw output repeated directly alongside its diagnostic, plus extra
internal state) needed to pin down the root cause, without requiring a
second run to get that detail after the fact. Repeat with `--quiet` also set,
to confirm debug mode's additions do not depend on live streaming having
occurred.

**Acceptance Scenarios**:

1. **Given** debug mode is off (the default), **When** a phase fails,
   **Then** the console shows the Story 2 diagnostic without repeating the
   failing step's raw output a second time (it either already streamed live,
   or — under `--quiet` — is available only via the log file).
2. **Given** debug mode is on, **When** a phase fails, **Then** the console
   additionally shows the full raw output inlined with the diagnostic and
   extra internal detail relevant to that failure, regardless of whether
   `--quiet` is also set.
3. **Given** debug mode is on, **When** a run completes successfully,
   **Then** the extra detail does not prevent the user from still being able
   to identify the phase sequence from Story 1 (debug mode adds detail, it
   does not replace or obscure phase visibility).

---

### Edge Cases

- What happens when a failure occurs before any phase has been clearly
  announced (e.g. a crash during startup/argument parsing)? The user should
  still get an actionable message, even if no pipeline phase had started yet.
- What happens when the same underlying step fails repeatedly across retries
  (e.g. an agent repair loop that tries and fails more than once)? The
  diagnostic should reflect the final outcome without forcing the user to
  read every intermediate attempt to understand what ultimately happened.
- How does the system behave when console output is redirected to a file or
  CI log (non-interactive) rather than viewed live in a terminal? Phase and
  diagnostic information must remain readable plain text, not depend on
  features that only work in an interactive terminal.
- What happens when a phase succeeds but produces warnings or partial
  problems that don't fail the run? The phase should still be reported as
  completed, without being confused with an outright failure.
- What happens when `--quiet` is combined with a phase failure? The failure
  diagnostic (phase, step, message, origin, log path) must still appear in
  full — quiet mode only suppresses per-line raw streaming while a phase is
  running, it never suppresses phase banners or diagnostics.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST announce the start of each pipeline phase
  (repository ingestion, static analysis, static library build,
  agent-assisted library build repair, harness compile probe, agent-assisted
  harness repair, output generation) so the currently active phase is always
  identifiable from the console output.
- **FR-002**: The system MUST visually and textually distinguish an
  agent-assisted (LLM repair) phase from the deterministic/static phase that
  preceded it, so a user cannot mistake one for the other.
- **FR-003**: By default, the system MUST print each phase's full raw
  underlying build/tool output to the console as it streams (matching
  today's behavior), bracketed by a start and end announcement visible and
  distinguishable enough that a user is never left guessing which phase
  produced a given line of streamed output.
- **FR-004**: The system MUST preserve the complete raw output of every
  phase's underlying commands somewhere the user can retrieve it after the
  run, regardless of whether that raw output was printed to the console
  during the run.
- **FR-005**: When a phase fails, the system MUST present a diagnostic that
  identifies: which phase failed, a specific failing step within that phase,
  a human-readable description of the failure, and where to find the
  complete raw output for that failure.
- **FR-006**: When a failure diagnostic is shown, the system MUST make clear
  whether the failure came from a deterministic step or from an
  agent-assisted repair attempt.
- **FR-007**: If more than one phase fails within a single run (e.g. a
  deterministic step fails and a subsequent repair attempt also fails), the
  system MUST present the failures in the order they occurred so the user
  can distinguish the initial failure from a follow-up failure.
- **FR-008**: The system MUST provide a way for the user to opt into a debug
  mode that reveals additional diagnostic context (the full raw output of the
  failing step inlined directly with its diagnostic, plus internal
  implementation-level detail) beyond the default phase and diagnostic
  information, on top of — not instead of — FR-001–FR-007. This is
  independent of whether the user has also enabled quiet mode (FR-011).
- **FR-009**: Phase and diagnostic output MUST remain fully readable when the
  console output is redirected to a file or consumed non-interactively (e.g.
  in CI), without relying solely on features (such as terminal cursor
  control) that only function in an interactive terminal.
- **FR-010**: A failure that occurs before any pipeline phase has started
  MUST still produce an actionable message identifying that startup/setup is
  where the failure occurred.
- **FR-011**: The system MUST provide a way for the user to opt into a quiet
  mode that suppresses per-line raw subprocess output while keeping phase
  start/end announcements and failure diagnostics (FR-001–FR-007) fully
  visible; this is the mechanism by which a user can get the concise view
  that used to be this feature's default.

### Key Entities

- **Phase**: A named, ordered stage of a `generate` run (e.g. repository
  ingestion, static analysis, static library build, agent-assisted library
  repair, harness compile probe, agent-assisted harness repair, output
  generation). Has a name, a status (in progress, succeeded, failed), and a
  start/end time.
- **Failure Diagnostic**: The information shown to the user when a phase
  fails: which phase, which specific step within it, a human-readable
  description, and a reference to where the complete raw output can be
  found.
- **Debug Mode**: A user-selectable setting that, when enabled, adds
  additional diagnostic detail — the failing step's full raw output inlined
  directly with its `FailureDiagnostic`, plus internal implementation-level
  logging — to what is shown for a failed phase, without changing which
  phases run or what they do. Independent of Quiet Mode: either, both, or
  neither may be active in a given run.
- **Quiet Mode**: A user-selectable setting (`--quiet`) that suppresses
  per-line raw subprocess output while a phase is running, while leaving
  phase start/end announcements and failure diagnostics unchanged. This is
  the opt-in "concise" view; it is off by default, so a default run streams
  full raw output between clearly announced phase boundaries.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Given only the phase start/end announcements in the console
  output of a completed `generate` run (default or `--quiet`), a user can
  correctly list every phase the run passed through, in order, without
  needing to read or parse the underlying build tool's own output.
- **SC-002**: Given the console output of a failed `generate` run, a user can
  correctly identify which phase and which step failed within a few seconds
  of reading it, by locating the failure diagnostic rather than needing to
  interpret streamed raw build output themselves.
- **SC-003**: A user who runs with `--quiet` sees noticeably less console
  output than the default (or than today's undifferentiated stream) while
  still identifying every phase; a user on the default (verbose) mode sees
  the same raw output as today, now clearly bounded by phase announcements.
- **SC-004**: With debug mode enabled, a user investigating a failure can
  find the specific underlying command or output responsible without needing
  to re-run HarnessBuddy a second time to capture it, whether or not
  `--quiet` was also set.
- **SC-005**: A user reading a failure diagnostic for an agent-assisted
  repair attempt can tell, without ambiguity, that a repair was attempted and
  did not succeed (as opposed to no repair having been attempted at all).

## Assumptions

- "Agentic library build" and "agentic harness build" in the request refer to
  the existing LLM-driven repair attempts that already run after a
  deterministic library build or harness compile probe fails; this feature
  makes those existing phases clearly visible and labeled, it does not add
  new repair behavior.
- By default, the console shows the full raw stream of every underlying
  command exactly as it does today, bracketed by clear phase start/end
  announcements — conciseness is opt-in via `--quiet`, not the default. This
  reverses an earlier draft of this spec (which defaulted to a condensed view
  and made raw streaming opt-in via `--log-level debug`); the direction was
  changed based on explicit user feedback that they want to keep seeing live
  build output by default and just need phase boundaries made unmistakable.
  The complete raw output is, regardless of mode, always additionally
  persisted to a per-phase log file (FR-004).
- "A clear place in the code they can point to" is interpreted as the phase
  and step identity of a failure (e.g. "static library build" → "cmake
  configure step"), not literal source file/line numbers, since the audience
  for this output is the CLI end user rather than a HarnessBuddy developer
  reading a stack trace.
- Debug mode is a single on/off toggle rather than multiple verbosity tiers,
  matching the phrasing "turning on debugging" in the request.
- This feature is scoped to the `generate` command's own console output; it
  does not change the underlying build/verification logic, the content of
  OSS-Fuzz Docker build logs consumed by outside tooling, or the generated
  project artifacts themselves.
