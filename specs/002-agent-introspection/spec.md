# Feature Specification: Agent Run Introspection

**Feature Branch**: `002-agent-introspection`

**Created**: 2026-07-01

**Status**: Draft

**Input**: User description: "I want to add additional introspection for when we pull the
agent into the loop. I would like to be able to stream its output to the terminal, but
--output-format=stream-json is not easy to read for the user. I would also like to add
stat tracking like \"agent time\", \"agent cost\", etc."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Follow agent activity in real time, in plain language (Priority: P1)

A user runs `harnessbuddy generate` with an LLM agent enabled (`--agent claude` or
`--agent codex`) as a fallback for a failed build or harness link. While the agent works,
the user watches the terminal to understand what it is doing — which files it is
inspecting or editing, which commands it is running, and what it currently believes the
problem is — expressed as readable narration rather than raw structured event data.

**Why this priority**: This is the core complaint driving the feature — today's output is
either silent or a wall of machine-formatted events, so the user cannot tell whether the
agent is making progress, stuck, or about to do something unexpected. Without this, the
user has no way to supervise a multi-minute automated repair.

**Independent Test**: Can be fully tested by running `generate` against a repository whose
static build is known to fail with `--agent claude` (or `--agent codex`) configured, and
observing that the terminal shows a readable narrative of the agent's actions while it
runs, with no raw structured event syntax visible.

**Acceptance Scenarios**:

1. **Given** a build that requires agent repair, **When** the agent is invoked, **Then**
   the terminal shows a live, human-readable account of the agent's actions as they
   happen (not a static message that only appears after completion).
2. **Given** an agent run in progress, **When** the agent reads a file, edits a file, or
   runs a shell command, **Then** the terminal reflects that specific action in plain
   language rather than as an unformatted data structure.
3. **Given** an agent run that fails or is cut short, **When** the user reviews the
   terminal output, **Then** the full underlying agent output remains available for
   diagnosing the failure (readability MUST NOT come at the cost of losing diagnostic
   detail).

---

### User Story 2 - See how long an agent run took and what it cost (Priority: P2)

After an agent repair invocation finishes — whether it succeeds, fails, or is cut short by
a usage/rate limit — the user sees a short summary reporting how long the agent ran and,
when the agent backend reports it, how much the run cost in real money. This lets the user
monitor and budget spend across repeated `generate` runs, since each agent invocation can
incur real dollar cost.

**Why this priority**: Cost and time are the two things a user needs to decide whether to
keep using the agent fallback at all; without visibility into them, agent usage is a black
box the user cannot budget for. This builds on User Story 1 but is independently
valuable even if the live narration is not yet in place.

**Independent Test**: Can be fully tested by running `generate` with an agent configured,
letting the invocation complete (success, failure, or budget-limit), and confirming a
summary line reporting elapsed time (and cost, when available) is shown for that
invocation.

**Acceptance Scenarios**:

1. **Given** an agent invocation that completes successfully, **When** it finishes,
   **Then** the user sees the elapsed wall-clock time for that invocation.
2. **Given** an agent invocation using a backend that reports cost, **When** it finishes,
   **Then** the user sees the monetary cost of that invocation.
3. **Given** an agent invocation using a backend that does not report cost but does
   report token usage, **When** it finishes, **Then** the user sees the token usage
   (e.g. input/output token counts) for that invocation in place of a dollar cost,
   rather than a missing or misleading value.
4. **Given** an agent invocation that fails or hits a usage/rate limit, **When** it ends,
   **Then** the elapsed time (and cost, if available) up to that point is still reported.
5. **Given** an agent invocation has completed, **When** the user later inspects the run's
   output directory, **Then** a file is present containing that invocation's readable
   transcript and its time/cost summary, matching what was streamed live.

---

### Edge Cases

- What happens when the agent backend emits output the tool cannot interpret as
  structured events (e.g. a malformed or unexpected line)? The user must still see that
  output rather than have it silently dropped.
- What happens when an agent invocation times out? The time/cost summary must still be
  shown using whatever data was captured before the timeout.
- What happens when the two different agent backends (`claude`, `codex`) report their
  activity and cost data in different shapes? Both must produce equivalent readable
  narration and summaries to the user.
- What happens during a `generate` run that invokes the agent twice (once for the library
  build, once for the harness link probe)? Each invocation must get its own readable
  narration and its own time/cost summary.
- What happens when a backend reports neither a dollar cost nor token usage? The user
  must see an explicit "no cost/usage data available" indication (FR-010), not a blank
  or zeroed-out line.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST render an agent invocation's activity to the terminal in
  human-readable form while the invocation is in progress, instead of showing raw
  structured/JSON event data.
- **FR-002**: The human-readable rendering MUST reflect the agent's actual actions (files
  read or edited, commands run, and its narrative status/reasoning) as they occur, not
  only a final result once the invocation ends.
- **FR-003**: System MUST measure the wall-clock duration of each agent invocation.
- **FR-004**: System MUST report the wall-clock duration to the user after each agent
  invocation ends, regardless of whether it succeeded, failed, or was stopped by a
  usage/rate limit.
- **FR-005**: System MUST report the monetary cost of an agent invocation when the
  underlying agent backend provides cost data.
- **FR-006**: When the underlying agent backend does not provide cost data but does
  provide token usage data (e.g. input/output token counts), System MUST report that
  token usage as a substitute metric instead of only stating cost is unavailable.
- **FR-007**: System MUST apply this introspection (readable streaming and time/cost
  reporting) uniformly to every point where an agent is invoked (both the library-build
  repair agent and the harness-build repair agent).
- **FR-008**: System MUST retain the complete, unabridged agent output produced during an
  invocation so that existing failure-diagnosis behavior (detecting a user-actionable
  roadblock or a usage-limit condition) continues to work unchanged.
- **FR-009**: System MUST, in addition to streaming it live, persist the readable
  transcript and the time/cost summary for each agent invocation to a file within that
  run's output/work directory, so the user can review it after the run ends.
- **FR-010**: When an agent backend provides neither cost nor token usage data, System
  MUST explicitly indicate that no cost/usage data is available rather than omitting the
  field or showing a misleading value (e.g. zero).

### Key Entities

- **Agent Invocation Record**: Represents one call to an agent backend to repair a
  failure. Carries which backend was used, how long it ran, its outcome (succeeded,
  failed, hit a usage limit, timed out), its cost when known, and its token usage
  (input/output token counts) when known — token usage is surfaced as a substitute
  metric when the backend does not report cost.
- **Agent Activity Event**: A single unit of the agent's readable narration (e.g. "read
  file X", "ran command Y", "editing file Z", a status update) surfaced live during an
  invocation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user watching the terminal during an agent repair run can describe what
  the agent is currently doing at any point without needing to interpret structured data
  syntax.
- **SC-002**: 100% of agent invocations — successful, failed, or stopped by a usage
  limit — end with a visible summary reporting elapsed time.
- **SC-003**: 100% of agent invocations report a dollar cost when the agent backend
  provides one; when it does not but reports token usage, 100% report that token usage
  instead; none silently omit both the cost and usage information.
- **SC-004**: A user can state how long an agent run took, and its cost if applicable,
  within 5 seconds of the run finishing, without consulting any output other than what
  was already shown in the terminal.
- **SC-005**: No agent-invocation failure investigation loses diagnostic detail compared
  to today's behavior as a result of switching to readable rendering.
- **SC-006**: After a `generate` run finishes, a user can open one file per agent
  invocation and see the same transcript and time/cost summary that was streamed live,
  without needing to have captured the terminal output themselves.

## Assumptions

- The agent backends already in use (`claude`, `codex`) are the only backends in scope;
  no new agent backend is being added by this feature.
- "Cost" means the monetary cost of the LLM usage for that invocation, as reported by the
  agent backend itself — this feature does not add its own independent cost estimation
  when the backend provides none.
- Readable narration replaces what the user currently sees (raw passthrough or silence);
  it does not need to reproduce every low-level structured event verbatim, only convey
  the agent's actions and status faithfully.
- This feature does not change when or how many times an agent is invoked (no new retry
  logic) — it only changes what the user sees during and after invocations that already
  happen today.
