"""Phase reporting and failure diagnostics for the `generate` console output.

See specs/012-clear-build-logging/ for the feature this module implements:
`Phase`/`PhaseExecution`/`RunReport` track which pipeline stage is running and how it
ended; `PhaseReporter` brackets a phase's console output with a start/end banner
(FR-001/FR-002); `FailureDiagnostic` and its builder/formatter turn a phase's failure
into the concise, located summary described by FR-005/FR-006.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Literal

_BANNER_WIDTH = 70
_DEFAULT_MESSAGE_LINES = 2


class Phase(Enum):
    """Ordered, fixed set of pipeline stages a `generate` run passes through."""

    INGESTION = "ingestion"
    STATIC_ANALYSIS = "static_analysis"
    STATIC_LIBRARY_BUILD = "static_library_build"
    AGENT_LIBRARY_REPAIR = "agent_library_repair"
    HARNESS_COMPILE_PROBE = "harness_compile_probe"
    AGENT_HARNESS_REPAIR = "agent_harness_repair"
    OUTPUT_GENERATION = "output_generation"


_PHASE_LABELS: dict[Phase, str] = {
    Phase.INGESTION: "Repository ingestion",
    Phase.STATIC_ANALYSIS: "Static analysis",
    Phase.STATIC_LIBRARY_BUILD: "Static library build",
    Phase.AGENT_LIBRARY_REPAIR: "Agent-assisted library repair",
    Phase.HARNESS_COMPILE_PROBE: "Harness compile probe",
    Phase.AGENT_HARNESS_REPAIR: "Agent-assisted harness repair",
    Phase.OUTPUT_GENERATION: "Output generation",
}

_AGENT_PHASES = frozenset({Phase.AGENT_LIBRARY_REPAIR, Phase.AGENT_HARNESS_REPAIR})


def phase_label(phase: Phase) -> str:
    """Return the console label for phase (data-model.md `Phase` table)."""
    return _PHASE_LABELS[phase]


def is_agent_phase(phase: Phase) -> bool:
    """Whether phase is one of the agent-assisted repair phases (FR-002)."""
    return phase in _AGENT_PHASES


PhaseStatus = Literal["running", "succeeded", "failed"]


@dataclass
class PhaseExecution:
    """One instance per phase actually run in a given `generate` invocation."""

    phase: Phase
    status: PhaseStatus = "running"
    started_at: float = 0.0
    ended_at: float | None = None
    log_path: Path | None = None

    def mark_succeeded(self) -> None:
        self._transition("succeeded")

    def mark_failed(self) -> None:
        self._transition("failed")

    def _transition(self, status: PhaseStatus) -> None:
        if self.status != "running":
            raise ValueError(f"cannot transition a {self.status!r} PhaseExecution to {status!r}")
        self.status = status
        self.ended_at = time.monotonic()


@dataclass
class FailureDiagnostic:
    """Shown to the user when a phase fails (spec.md Key Entities)."""

    phase: Phase
    step: str
    message: str
    origin: Literal["deterministic", "agent"]
    log_path: Path | None = None
    exit_code: int | None = None


@dataclass
class RunReport:
    """Aggregates every `PhaseExecution` (and `FailureDiagnostic`) for one `generate`
    invocation, in the order phases ran. Printed incrementally by `PhaseReporter` as
    each phase starts/ends rather than held back to the end; not itself persisted to
    disk (data-model.md `RunReport`)."""

    phases: list[PhaseExecution] = field(default_factory=list)
    diagnostics: list[FailureDiagnostic] = field(default_factory=list)

    def add_phase(self, execution: PhaseExecution) -> None:
        self.phases.append(execution)

    def add_diagnostic(self, diagnostic: FailureDiagnostic) -> None:
        self.diagnostics.append(diagnostic)


def summarize_message(text: str, *, max_lines: int = _DEFAULT_MESSAGE_LINES) -> str:
    """Collapse raw command output down to a short, human-readable diagnostic message:
    the last max_lines non-blank lines, which is usually where the actual error is."""
    non_blank = [line for line in text.splitlines() if line.strip()]
    if not non_blank:
        return "(no output captured)"
    return "\n".join(non_blank[-max_lines:])


def build_diagnostic(  # noqa: PLR0913 -- one param per FailureDiagnostic field
    phase: Phase,
    *,
    step: str,
    message: str,
    origin: Literal["deterministic", "agent"],
    log_path: Path | None = None,
    exit_code: int | None = None,
) -> FailureDiagnostic:
    """Construct a `FailureDiagnostic` — the single place these are assembled, so every
    call site produces the same shape (data-model.md `FailureDiagnostic`)."""
    return FailureDiagnostic(
        phase=phase,
        step=step,
        message=message,
        origin=origin,
        log_path=log_path,
        exit_code=exit_code,
    )


def _banner_line(phase: Phase, marker: str) -> str:
    fill = "#" if is_agent_phase(phase) else "="
    prefix = "AGENT: " if is_agent_phase(phase) else ""
    core = f" {prefix}{phase_label(phase)} [{marker}] "
    pad = max(3, (_BANNER_WIDTH - len(core)) // 2)
    return f"{fill * pad}{core}{fill * pad}"


def format_phase_start_banner(phase: Phase) -> str:
    """The line printed before a phase's own output begins (FR-001)."""
    return _banner_line(phase, "START")


def format_phase_end_banner(phase: Phase, status: Literal["succeeded", "failed"]) -> str:
    """The line printed once a phase concludes (FR-001)."""
    return _banner_line(phase, status.upper())


def format_diagnostic(
    diagnostic: FailureDiagnostic, *, debug: bool = False, raw_output: str | None = None
) -> str:
    """Render a `FailureDiagnostic` per contracts/cli-console-contract.md.

    When debug is set and raw_output is non-empty, the failing step's full raw output
    is inlined directly with the diagnostic (FR-008), independent of whether it also
    already streamed live or was suppressed by `--quiet`.
    """
    origin_text = (
        "agent repair attempt failed" if diagnostic.origin == "agent" else "build step failed"
    )
    lines = [
        f"--- FAILURE: {phase_label(diagnostic.phase)} ---",
        f"Step: {diagnostic.step}",
        f"Message: {diagnostic.message}",
        f"Origin: {origin_text}",
    ]
    if diagnostic.exit_code is not None:
        lines.append(f"Exit code: {diagnostic.exit_code}")
    log_text = str(diagnostic.log_path) if diagnostic.log_path is not None else "(not captured)"
    lines.append(f"Full output: {log_text}")
    if debug and raw_output:
        lines.append("--- Full raw output (--log-level debug) ---")
        lines.append(raw_output)
        lines.append("--- end raw output ---")
    return "\n".join(lines)


def format_startup_failure(message: str) -> str:
    """FR-010: a failure before any Phase has started still needs an actionable message."""
    return f"--- STARTUP FAILURE ---\n{message}"


class PhaseReporter:
    """Brackets one phase's console output with a start/end banner (FR-001/FR-002).

    Use as a context manager::

        with PhaseReporter(Phase.STATIC_LIBRARY_BUILD) as reporter:
            result = do_the_thing()
        if result.succeeded:
            reporter.succeed()
        else:
            reporter.fail()

    If the `with` block raises before `.succeed()`/`.fail()` is called, `__exit__`
    marks the phase failed and prints the end banner automatically, without
    swallowing the exception — so callers that fail via exception don't have to
    remember to call `.fail()` themselves.
    """

    def __init__(self, phase: Phase, *, run_report: RunReport | None = None) -> None:
        self.phase = phase
        self.execution = PhaseExecution(phase=phase, started_at=time.monotonic())
        self._run_report = run_report

    def __enter__(self) -> PhaseReporter:
        print(format_phase_start_banner(self.phase))
        if self._run_report is not None:
            self._run_report.add_phase(self.execution)
        return self

    def set_log_path(self, log_path: Path | None) -> None:
        self.execution.log_path = log_path

    def succeed(self) -> None:
        self.execution.mark_succeeded()
        print(format_phase_end_banner(self.phase, "succeeded"))

    def fail(self) -> None:
        self.execution.mark_failed()
        print(format_phase_end_banner(self.phase, "failed"))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        if exc_type is not None and self.execution.status == "running":
            self.fail()
        return False
