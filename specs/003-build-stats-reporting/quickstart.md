# Quickstart: Validating Build Statistics Reporting

## Prerequisites

- `uv run harnessbuddy generate <REPO_URL>` already works end-to-end for at least one
  repo that builds cleanly, and one repo whose static build/harness step fails.
- For agent scenarios: `claude` and/or `codex` CLI available on `PATH` and
  authenticated, and `--agent claude`/`--agent codex` already work end-to-end.

## Scenario 1 — Clean success, no agents (US1, SC-001, SC-004)

1. Run: `uv run harnessbuddy generate <REPO_URL>` against a repo that builds and
   harness-links successfully without agent help.
2. Open `<output>/<project_name>/output/stats.json`.
3. **Expected**: `status` is `"success"`; both `library_build_agent` and
   `harness_build_agent` have `invoked: false` and `duration_seconds`, `cost_usd`,
   `summary` all equal to the literal string `"N/A"`; `total_duration_seconds` is a
   positive number.

## Scenario 2 — Library agent repairs the build (US1, SC-001, SC-005)

1. Run: `uv run harnessbuddy generate <REPO_URL> --agent claude` against a repo whose
   deterministic library build fails but the agent can fix.
2. Open `stats.json` once the run finishes.
3. **Expected**: `library_build_agent.invoked` is `true`, with a numeric
   `duration_seconds`, a numeric `cost_usd` (Claude reports cost), and a `summary`
   string describing what the agent changed, without opening
   `agent_library_build.log`. `harness_build_agent` still reads all `"N/A"` if the
   harness step didn't need the agent. `status` is `"success"`.

## Scenario 3 — Harness agent invoked via a backend with no cost figure (SC-004)

1. Run with `--agent codex` against a repo whose harness link probe fails but Codex can
   fix.
2. **Expected**: `harness_build_agent.invoked` is `true`, `duration_seconds` is a
   number, but `cost_usd` reads `"N/A"` (Codex never reports a dollar cost) — not `0`,
   not blank.

## Scenario 4 — Unrecoverable library build failure (US1, edge case, FR-012)

1. Run against a repo whose build fails and cannot be fixed (with or without
   `--agent`).
2. **Expected**: the command exits non-zero as it does today, but
   `<output>/<project_name>/output/stats.json` still exists, with `status` equal to
   `"failed_library_build"` and (if an agent was invoked) its real duration/cost/summary
   recorded rather than `"N/A"`.

## Scenario 5 — Harness failure that still emits stub output (clarified behavior, FR-007)

1. Run against a repo whose library build succeeds but whose harness link probe fails
   in a way that isn't fixed (agent unavailable, or agent can't fix it), so the pipeline
   logs the failure and still writes stub harness files rather than halting.
2. **Expected**: `local/` and `oss-fuzz/` are still populated with stub harness content
   as today, **and** `stats.json` reports `status: "failed_harness_build"` — not
   `"success"` — even though an output directory was produced.

## Scenario 6 — Re-running into the same output directory (US2, FR-010)

1. Run once (any outcome), note `stats.json`'s contents.
2. Run again against the same output directory (accepting the overwrite prompt, or
   non-interactively).
3. **Expected**: `stats.json` reflects only the second run's numbers — no leftover or
   merged fields from the first run.

## Scenario 7 — Failure before an output directory exists (edge case, negative test)

1. Run against a URL that fails repository ingestion, or a repo with no detectable
   C/C++ build system (`UnsupportedRepositoryError`).
2. **Expected**: the command exits non-zero as today, and no `stats.json` is produced
   anywhere, since no output directory was ever established.

## Automated coverage

Equivalent behavior is exercised in:
- `tests/library_builder/test_stats.py` (new) — pure unit tests for
  `RunStats`/`AgentPhaseStats` construction and JSON serialization, covering every
  invoked/not-invoked/cost-unavailable/summary-unavailable combination without spawning
  a subprocess.
- `tests/test_cli.py` (updated) — asserts `stats.json` is written at the correct path
  with the correct `status` for: clean success, agent-repaired success, unrecoverable
  library failure, and harness failure with stub output, reusing the existing pattern of
  patching `build_library`/`build_harness` with fake results.
- `tests/core/test_agent_stream.py` (updated) — asserts the new `final_message` field on
  `AgentStreamResult` is populated from the last genuine assistant text block and is
  `None` when no such block appears in the stream, for both Claude and Codex payload
  shapes.
