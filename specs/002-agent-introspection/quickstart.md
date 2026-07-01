# Quickstart: Validating Agent Run Introspection

## Prerequisites

- `uv run harnessbuddy generate <REPO_URL> --agent claude` (or `--agent codex`) already
  works end-to-end against a repository whose static build fails, so the agent fallback
  path is exercised.
- `claude` and/or `codex` CLI available on `PATH` and authenticated.

## Scenario 1 — Live readable narration (US1, SC-001)

1. Pick (or construct) a small C/C++ repo whose deterministic build fails for a reason
   the agent can plausibly fix (e.g. a missing CMake option).
2. Run: `uv run harnessbuddy generate <REPO_URL> --agent claude`
3. While the agent is working, watch the terminal.
4. **Expected**: the terminal shows readable lines describing the agent's actions
   (reading/editing files, running commands, status updates) as they happen — no raw
   JSON objects, no silent multi-minute gap with nothing printed.

## Scenario 2 — Duration and cost summary (US2, SC-002, SC-003)

1. Let the Scenario 1 run finish (success or failure).
2. **Expected**: immediately after the agent invocation ends, the terminal shows the
   elapsed wall-clock time, and — because Claude reports cost — a dollar cost line.
3. Repeat with `--agent codex` on a case that also reaches the agent.
4. **Expected**: elapsed time is still shown; since Codex reports no dollar cost, the
   cost line is replaced by a token usage line (e.g. `tokens: input=1234 output=567`,
   see `contracts/agent-run-report.md`) rather than being blank, `$0`, or a bare
   "unavailable" with no other signal.

## Scenario 3 — Persisted report file (FR-009, SC-006)

1. After either run above finishes, inspect the project's workspace directory
   (`.harnessbuddy/<project>/`).
2. **Expected**: `agent_library_build.log` (or `agent_harness_build.log`, depending on
   which repair ran) exists, containing the same transcript lines shown live, followed by
   the `=== Agent Run Summary ===` block per `contracts/agent-run-report.md`, matching
   what was printed to the terminal.

## Scenario 4 — Diagnostics preserved on failure (FR-008, SC-005)

1. Force a case where the agent cannot fix the build (e.g. an unfixable repo, or a
   deliberately impossible instruction) so `BuildFailureError` is raised, or trigger a
   `LLMBudgetError` (e.g. a rate-limited API key).
2. **Expected**: the raised exception's message still contains the full underlying agent
   output needed to diagnose the failure — the same detail available before this feature,
   not reduced by the switch to readable rendering.

## Scenario 5 — Malformed/unexpected stream line (edge case)

1. This is best exercised as an automated test rather than manually: feed the renderer a
   line that is not valid JSON (or valid JSON that doesn't match a known event shape).
2. **Expected**: that line is still surfaced (both live and in the persisted transcript)
   verbatim rather than silently dropped, and parsing continues for subsequent lines.

## Automated coverage

Equivalent behavior is exercised in:
- `tests/core/test_agent_stream.py` (new) — pure parsing/rendering unit tests, including
  the malformed-line fallback case, without spawning a real subprocess.
- `tests/library_builder/test_agents.py` (updated) — asserts `duration_seconds`,
  `cost_usd`, and `transcript_path` are populated on the result dataclasses for both
  backends, and that the report file is written, using the existing pattern of patching
  the core streaming runner with a fake `claude`/`codex` JSONL payload.
