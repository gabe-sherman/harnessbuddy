# Contract: `harnessbuddy generate` CLI surface

## New flag

```
--environment {local,oss-fuzz}   Target environment to build and validate each stage in.
                                  Default: local.
```

- Choices are exactly `local` and `oss-fuzz` (FR-001, FR-013 — no `custom` choice).
- Omitting the flag is equivalent to `--environment local` (FR-002) — every existing
  invocation of `harnessbuddy generate` keeps its current behavior unchanged.

## Interaction with existing flags

| Existing flag | Interaction |
|---|---|
| `--agent {codex,claude}` | Unaffected. Repair agents run the same way regardless of environment; only the verification command they're told to run changes (see `agent-scripts.md`). |
| `--no-agents` | Unaffected. Disables agent fallback for both environments equally (FR-011). |
| `--skip-validation` | Extended meaning: also skips the per-stage environment gate — both stages still run (their output is still needed for generation), but a failing stage no longer stops the pipeline before generation. |
| `--output`, `--project-name`, `--repo-ref`, `--keep-workdir` | Unaffected. |

## Exit behavior

| Condition | Exit code | Agent fallback? |
|---|---|---|
| `--environment oss-fuzz` selected, Docker daemon unreachable (`docker info` fails) | 1, actionable message naming the missing dependency | No (FR-012) |
| `--environment oss-fuzz` selected, probe-image build fails due to network/pull error | 1, actionable message | No (FR-012) |
| A stage fails validation in the selected environment (build-logic failure) | 1, per-stage/per-environment diagnostic (FR-007) | Yes, if `--agent` given and it's not a budget/availability error |
| All stages pass | 0, final report states the environment used (SC-001) | N/A |

## Final report additions

The existing end-of-run report (`_generate_outputs` / final print block in `cli.py`) gains
one line stating the selected environment, and `stats.json` gains the `environment` field
described in `data-model.md`.
