# Contract: Persisted Agent Run Report

Each agent invocation (library-build repair or harness-build repair, either backend)
writes exactly one plain-text file to the project's workspace directory
(`.harnessbuddy/<project>/`, the same directory already passed to
`invoke_library_builder_agent`/`invoke_harness_builder_agent` as `workdir`). This is the
artifact required by FR-009 / SC-006 — a user can open it after the run ends and see the
same content that was streamed live.

## File naming

| Invocation | File name |
|---|---|
| Library build repair | `agent_library_build.log` |
| Harness build repair | `agent_harness_build.log` |

A given project workspace has at most one of each — a second invocation of the same kind
within the same workspace overwrites the previous file (matches existing behavior of
`build_library.sh`/`build_harness.sh` being overwritten on re-run; this is not an append
log).

## Content structure

The file contains, in order:

1. **Transcript section** — one line per `AgentActivityEvent` rendered during the
   invocation, in the order they occurred. This is the same text that was printed to the
   terminal live.
2. **Summary section** — a fixed-format trailer, written once the invocation ends
   (success, failure, budget limit, or timeout):

```text
=== Agent Run Summary ===
backend: <claude|codex>
outcome: <succeeded|failed|budget_limited|timed_out>
duration: <N.Ns>
cost: <$N.NNNN>
```

or, when the backend does not report a dollar cost but does report token usage
(FR-006 — today, this is the Codex path):

```text
=== Agent Run Summary ===
backend: <claude|codex>
outcome: <succeeded|failed|budget_limited|timed_out>
duration: <N.Ns>
tokens: input=<N> output=<N>
```

or, when the backend reports neither (FR-010 — not hit by either backend today, kept for
robustness):

```text
=== Agent Run Summary ===
backend: <claude|codex>
outcome: <succeeded|failed|budget_limited|timed_out>
duration: <N.Ns>
cost: unavailable
```

Field rules:
- `backend` is the `tool` argument passed to the invocation (`claude` or `codex`) —
  matches Constitution Principle VI's requirement that every invocation declares its
  target tool explicitly.
- `outcome` reflects the same determination used elsewhere (`succeeded` from
  `result.succeeded`; `budget_limited` when `LLMBudgetError` was raised; `timed_out` when
  the underlying run hit its timeout; `failed` otherwise).
- `duration` is `duration_seconds` formatted to one decimal place, always present.
- The trailer's last line is exactly one of: a `cost: $N.NNNN` line (4 decimal places)
  when `cost_usd` is known; a `tokens: input=N output=N` line when `cost_usd` is `None`
  but `input_tokens`/`output_tokens` are known; or `cost: unavailable` when none of the
  three values are known. Never blank, never `$0.0000` or `input=0 output=0` as a
  stand-in for unknown.

## Consumers

- **Primary**: a human user reviewing a past run.
- **Not in scope for this feature**: no other part of the pipeline (deterministic build
  logic, OSS-Fuzz project generation) reads this file — it is a diagnostic/observability
  artifact only, not an input to any decision.
