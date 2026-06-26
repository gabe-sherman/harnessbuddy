# Task 10: Agent Fallback Orchestration

**Status**: pending

## Summary

Add Auditron-style focused agent fallback for build script and harness
compilation repair, without any of Auditron's long-running infrastructure.

## Requirements

- Model the fallback after Auditron's subprocess primitives:
  - an explicit agent spec containing tool, optional model, and reasoning effort
  - a focused prompt builder for one assigned repair task
  - a tool-specific non-interactive command builder
  - a `subprocess.Popen` launch with `stdin=subprocess.DEVNULL`
  - stdout/stderr capture to a run-state log file
  - a JSON process record containing handle, role, pid, command, cwd, log path,
    start/update timestamps, exit code, and runtime status
  - timeout handling that terminates the child process and marks the run failed
- Do **not** implement Auditron's long-running operator, scheduler, dashboard,
  SQLite workflow store, heartbeats, inbox nudging, or per-agent git worktrees.
- HarnessBuddy runs one short-lived fallback subprocess per repair attempt and
  waits for it to exit before re-running validation.
- Non-interactive Codex agents run through `codex exec`.
- Non-interactive Claude agents run through `claude --print`.
- The CLI chooses fallback behavior with `--agent auto|codex|claude`, defaulting
  to `auto`.
- Store focused fallback prompt templates in the library-builder package, not in
  the repo-level `prompts/` directory.
- Store process records and logs in HarnessBuddy run state.
- Use stable repair handles such as `build-script-fixer` and
  `harness-compile-fixer` so logs and JSON records are predictable.
- Add at least two focused fallback tasks:
  - fix library build scripts after library build failure
  - fix harness compilation after compile failure
- Agents may edit generated files directly.
- Fallback prompts must include:
  - the project output path
  - the repository URL and optional repo ref
  - the selected build system
  - relevant validation command and log excerpts
  - the exact files the agent is allowed to edit
  - instructions to make the smallest repair and not touch unrelated files
- HarnessBuddy must report whether validation passed after agent edits. Failed
  edits are left in place for inspection with logs and validation status.
- `--no-agents` must prevent all agent launches.

## Acceptance Criteria

- Fake `codex` and `claude` executables prove command construction.
- Tests prove logs and process records are written.
- Tests prove subprocess stdout and stderr are captured in the configured log path.
- Tests prove timeout handling marks the fallback attempt failed.
- Tests prove fallback prompts include the failing command, log excerpt, allowed
  files, and selected build system.
- Tests prove direct file edits are accepted only after validation passes.
- Tests prove `--no-agents` disables fallback.
