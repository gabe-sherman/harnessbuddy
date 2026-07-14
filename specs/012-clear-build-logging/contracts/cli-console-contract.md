# Contract: `generate` console output

HarnessBuddy is a CLI tool, so its externally observable "interface" is its
console output (stdout/stderr) and its flags, not a network API. This
contract defines what tests (and future readers) can rely on. It replaces
today's implicit, undocumented `print()` conventions in `cli.py`.

## Flag contract

- `harnessbuddy generate ... [--log-level {debug,info,warning,error,critical}] [--quiet]`
  — `--log-level` is the existing flag, kept as-is (see research.md
  Decision 2, revised). `--quiet` is new (research.md Decision 5, revised).
  No new `--debug` flag is introduced.
- The two flags are orthogonal and independently settable:
  - `--quiet` controls only whether per-line raw subprocess output streams
    live to the console while a phase runs. Default (flag absent): stream
    live, as today. With `--quiet`: suppress it.
  - `--log-level debug` controls only (a) whether a failed phase's
    diagnostic additionally inlines that phase's full raw output, and (b)
    whether Python's internal `logging` level is `DEBUG`. `info`/`warning`/
    `error`/`critical` retain their existing meaning (Python `logging`
    verbosity only) and MUST NOT change phase-banner, streaming, or
    diagnostic behavior beyond that.
- Any combination of the two flags MUST leave phase banners (below) and
  failure diagnostics visible; neither flag ever suppresses either.

## Phase banner contract (all flag combinations)

For every `Phase` that runs (data-model.md), stdout MUST contain, in order:

1. A start line identifying the phase by its console label
   (data-model.md `Phase` table), before any of that phase's own subprocess
   output would begin.
2. Exactly one end line per phase, indicating success or failure, once the
   phase concludes.

These two lines MUST appear regardless of `--log-level` or `--quiet`. This is
the part of the contract that satisfies spec FR-001/FR-002 and SC-001: a
reader of stdout can reconstruct the full, ordered phase sequence of a run by
scanning for these lines alone, whether or not raw output is also streaming
between them.

Both lines MUST use a separator distinctive enough to stand out from
whatever raw build-tool output streams between them by default (research.md
Decision 4 addendum) — reusing/extending the existing
`"=" * 25 + label + "=" * 25`-style banner already used for agent output.

Agent-assisted phases (`AGENT_LIBRARY_REPAIR`, `AGENT_HARNESS_REPAIR`) MUST
use a start/end line visually **and** textually distinguishable from
deterministic phases: a distinct label (e.g. "Agent-assisted library repair"
vs. "Static library build") **and** a distinct separator/fill character (e.g.
`"#"` instead of `"="`, plus an "AGENT:" prefix) — never wording alone. This
satisfies FR-002/SC-005 even while both phase types stream comparable raw
output in between.

## Console-volume contract (default vs. `--quiet`)

- **Default** (`--quiet` absent): per-line output from `run_command_streaming`
  MUST print live to the console exactly as it does today, bracketed by the
  phase banners above. This satisfies the revised FR-003.
- **`--quiet`**: per-line output from `run_command_streaming` MUST NOT print
  to the console. Phase banners and failure diagnostics are unaffected. This
  satisfies FR-011/SC-003's opt-in concise view.
- Regardless of `--quiet`, every phase's full raw stdout/stderr MUST still be
  written to its log file (data-model.md `PhaseExecution.log_path`),
  regardless of success or failure (FR-004).

## Failure diagnostic contract

When a phase's end line reports failure, stdout/stderr MUST additionally
include a diagnostic block containing, at minimum:

- The failed phase's console label.
- The `FailureDiagnostic.step` value (which specific step within the phase
  failed).
- The `FailureDiagnostic.message` (human-readable description).
- The `FailureDiagnostic.origin` (`deterministic` or `agent`), rendered as
  text a reader can distinguish without cross-referencing code (e.g. "agent
  repair attempt failed" vs. "build step failed").
- The `FailureDiagnostic.log_path`, when set, as a path the user can open.

If more than one phase fails within a single run, one diagnostic block MUST
be emitted per failed phase, in the order those phases failed (FR-007).

A failure that occurs before any `Phase` has started (e.g. argument parsing)
MUST still produce a message identifying that the failure occurred during
startup, even though no phase banner precedes it (FR-010).

## `--log-level debug` mode additions (on top of the above, never instead of)

When `--log-level debug` is set, regardless of `--quiet`:

- A failure diagnostic block MUST additionally include the failing phase's
  full raw stdout/stderr inline, not only the log file path — even if that
  same output already streamed live by default (this is a convenience so the
  user doesn't have to scroll back or open the log file), and especially
  when `--quiet` suppressed it from ever appearing otherwise.
- Python's internal `logging` level MUST be set to `DEBUG`, surfacing the
  existing `logger.debug(...)` call sites in `analysis.py`,
  `harness_explorer.py`, and `environments/oss_fuzz.py`.

## Non-interactive contract

All of the above MUST hold identically whether stdout is an interactive
terminal or redirected to a file/CI log (FR-009): no ANSI cursor-control
sequences, spinners, or "overwrite the previous line" behavior may be the
sole means of conveying phase or diagnostic information.

## Consumers of this contract

- `tests/test_cli.py` — asserts phase banners, diagnostic blocks, and
  `--log-level debug` behavior against captured stdout/stderr.
- `tests/library_builder/test_library_build.py` — asserts a real,
  unmocked build produces the expected `log_path` file.
