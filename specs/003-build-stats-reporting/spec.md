# Feature Specification: Build Statistics Reporting

**Feature Branch**: `003-build-stats-reporting`

**Created**: 2026-07-01

**Status**: Draft

**Input**: User description: "Good, now I actually want to improve upon our statistics reporting. Rather than just printing them to file, I would like a stats class for the library_builder that outputs statistics like total time, library build agent time and cost (if it was invoked, fill with "N/A" if not), harness build agent time and cost (if it was invoked, fill with "N/A" if not), and a final status (i.e., failed on library build, failed on harness build, success). These should be written to the output directory of the build so the user has an easy consistent way to view the stats. Probably a json output would be best" — plus a follow-up: "also I want to capture the LLM's summary of the work it did and store this to the corresponding agent statistic info."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Review run outcome and cost at a glance (Priority: P1)

A user runs `harnessbuddy generate` against a library. After the run finishes (whether it succeeded or failed partway through), they want to open a single, well-known file in the build's output directory and immediately see: how long the whole run took, whether an LLM agent was used to repair the library build (and if so, how long it took, what it cost, and a plain-language summary of what the agent did), whether an LLM agent was used to repair the harness build (and if so, how long it took, what it cost, and a plain-language summary of what the agent did), and the final outcome of the run.

**Why this priority**: This is the entire feature — a consistent, at-a-glance record of what happened and what it cost. Without it, users have to dig through log files and terminal scrollback to answer basic "did it work, and what did it cost me" questions.

**Independent Test**: Run `harnessbuddy generate` on a repository that builds successfully without needing agent repair. Confirm a stats file appears in the output directory reporting total run time, "N/A" for both agents' time and cost, and a "success" status.

**Acceptance Scenarios**:

1. **Given** a library build that succeeds on the first attempt with no agent invocation, **When** the run completes, **Then** the stats file reports total run time, `N/A` for library-build-agent time, cost, and work summary, `N/A` for harness-build-agent time, cost, and work summary, and a final status of success.
2. **Given** a library build that fails and is repaired by the LLM agent, and a harness build that succeeds without agent help, **When** the run completes, **Then** the stats file reports the library-build agent's time, cost, and a plain-language summary of the repair it performed (real values), `N/A` for the harness-build agent's time, cost, and work summary, and a final status of success.
3. **Given** a library build that fails and cannot be repaired by the LLM agent, **When** the run halts, **Then** the stats file reports the library-build agent's time, cost, and work summary, and a final status indicating the failure occurred during the library build.
4. **Given** a successful library build and a harness build that fails and cannot be repaired by the LLM agent, **When** the run halts, **Then** the stats file reports `N/A` for the library-build agent's time, cost, and work summary, real values for the harness-build agent's time, cost, and work summary, and a final status indicating the failure occurred during the harness build.

---

### User Story 2 - Consistent location across runs (Priority: P2)

A user (or a script they've written) automates repeated runs of `harnessbuddy generate` across many libraries. They want the stats file to always live at the same relative location within each run's output directory, so they can programmatically collect and compare results across many runs without special-casing each one.

**Why this priority**: A stats file that appears in a different place depending on run outcome, or that's missing entirely on failure, defeats the purpose of "an easy consistent way to view the stats." This priority ensures the feature is genuinely usable for batch/scripted workflows, not just a one-off manual check.

**Independent Test**: Run `harnessbuddy generate` across several libraries with a mix of outcomes (clean success, agent-repaired success, unrecoverable failure). Confirm the stats file is present at the same relative path in every run's output directory, using the same field names and shapes.

**Acceptance Scenarios**:

1. **Given** two separate runs with different outcomes (one succeeds cleanly, one fails during the harness build), **When** both complete, **Then** both output directories contain a stats file at the same relative path with the same field names, differing only in the field values and status.

---

### Edge Cases

- What happens if the run fails before an output directory can be created at all (e.g., the repository can't be cloned, or the build system can't be detected)? Since there is no output directory to write into, no stats file can be produced for that run.
- What happens if the harness-build phase reports an error but the pipeline still produces a (stubbed) output rather than halting? The stats file's final status is `failed_harness_build`, even though an output directory (with stub harness content) was still produced.
- What happens if the same output directory is reused for a second run (overwrite)? The stats file must be overwritten along with the rest of the output, not appended to or left stale from a previous run.
- What happens if the LLM agent is invoked but its process crashes or times out without ever reporting a cost? The stats file must still be produced with the time/cost fields reflecting what's known (e.g., duration captured up to the point of failure) rather than being omitted or crashing the reporting step itself.
- What happens if the LLM agent is invoked but never produces any final natural-language text before exiting (e.g., it crashes immediately)? The work-summary field must fall back to an explicit "unavailable" indicator rather than being blank or missing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST record, for every `harnessbuddy generate` run that reaches the point of creating an output directory, the total wall-clock duration of the run.
- **FR-002**: System MUST record whether the library-build LLM agent was invoked during the run.
- **FR-003**: When the library-build agent was invoked, system MUST record its duration, its cost, and a plain-language summary of the work it performed; when it was not invoked, all three fields MUST be reported as `N/A`.
- **FR-004**: System MUST record whether the harness-build LLM agent was invoked during the run.
- **FR-005**: When the harness-build agent was invoked, system MUST record its duration, its cost, and a plain-language summary of the work it performed; when it was not invoked, all three fields MUST be reported as `N/A`.
- **FR-006**: When an agent was invoked but no cost figure is available from that agent backend, system MUST report the cost as `N/A` rather than a fabricated or zero value.
- **FR-006a**: The work-summary field for an invoked agent MUST be derived from that agent's own final natural-language response (its last reported message before completing), not from the raw interleaved tool/file/command transcript.
- **FR-006b**: When an agent was invoked but never produced a final natural-language response before exiting (e.g., it crashed immediately), system MUST report the work-summary field as an explicit "unavailable" indicator rather than leaving it blank or omitting it.
- **FR-007**: System MUST record a final status for the run, distinguishing at minimum: overall success, failure during the library build, and failure during the harness build. A harness-build error is reported as a failed-harness-build status even when the pipeline does not halt and still emits stub output — the status reflects the health of the harness build itself, not merely whether the run reached completion.
- **FR-008**: System MUST write the recorded statistics to a single well-known file, in JSON format, at the parent output directory that contains the run's `local/` and `oss-fuzz/` subdirectories (not duplicated into each subdirectory).
- **FR-009**: The stats file's field names and structure MUST be identical across all runs regardless of outcome, so that automated tooling can parse any run's stats file the same way.
- **FR-010**: System MUST overwrite any pre-existing stats file when a run reuses the same output directory, rather than appending to it or leaving a stale file from a prior run.
- **FR-011**: System MUST NOT crash or abort the run if statistics cannot be fully collected for some reason (e.g., an agent process died before reporting its cost); it must instead record what is known and mark the remainder as unavailable.
- **FR-012**: System MUST attempt to record and write statistics even when the run ultimately fails, as long as an output directory exists to write into.

### Key Entities

- **Run Statistics**: A single record produced once per `harnessbuddy generate` invocation, capturing the total run duration, the library-build agent's involvement (invoked or not, plus its duration, cost, and work summary when invoked), the harness-build agent's involvement (invoked or not, plus its duration, cost, and work summary when invoked), and the final status of the run.
- **Agent Invocation Summary**: The time, cost, and work-summary text associated with one LLM agent invocation during a run (library-build or harness-build); either fully populated with real values, or entirely marked "N/A" when that agent was never invoked.
- **Final Status**: The outcome classification for a run — success, failed during library build, or failed during harness build — used to summarize at a glance where in the pipeline a run stopped.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After any `harnessbuddy generate` run that reaches the output-directory stage, a user can determine the run's total duration, agent costs, what each invoked agent did, and final outcome by reading a single file, without inspecting log files or terminal output.
- **SC-002**: The stats file uses the same field names and structure on 100% of runs, regardless of whether agents were invoked or the run succeeded or failed.
- **SC-003**: A user scripting across many runs can locate each run's stats file at the same relative path in every output directory, with no run-specific special-casing required.
- **SC-004**: When an LLM agent was not invoked for a given phase, the corresponding time, cost, and work-summary fields read exactly "N/A" — never blank, zero, or missing.
- **SC-005**: When an LLM agent was invoked for a given phase, a user can read that phase's work-summary field and understand, in plain language and without opening the full transcript, what the agent changed or attempted.

## Assumptions

- The stats file is scoped to a single `harnessbuddy generate` invocation; historical tracking or aggregation across multiple runs is out of scope for this feature.
- "Time" for the library-build and harness-build agents refers to the same agent-invocation duration already captured internally when an agent repairs a build (as distinct from the total run time, which spans the entire pipeline).
- If a run fails before any output directory is created (e.g., repository ingestion or build-system detection fails), no stats file is produced for that run, since there is no directory to write it into.
- Cost figures are denominated in US dollars, consistent with how agent cost is already reported elsewhere in the tool.
- This feature only concerns the statistics file's content and location; it does not change the existing per-agent transcript/log files, which continue to be written as they are today.
- "The LLM's summary of the work it did" refers to that agent's own final natural-language message before it finished running (e.g., its closing explanation of what it changed and why) — not a separately-prompted summarization step, and not the raw combined transcript of every tool call and file edit.
