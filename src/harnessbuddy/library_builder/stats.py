from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from harnessbuddy.core.agent_stream import AgentRunSummary
from harnessbuddy.library_builder.models import (
    AgentReport,
    BuildExplorationResult,
    HarnessExplorationResult,
)


class RunStatus(Enum):
    SUCCESS = "success"
    FAILED_LIBRARY_BUILD = "failed_library_build"
    FAILED_HARNESS_BUILD = "failed_harness_build"


@dataclass
class AgentPhaseStats:
    invoked: bool
    duration_seconds: float | str
    cost_usd: float | str
    input_tokens: int | str
    output_tokens: int | str
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "invoked": self.invoked,
            "duration_seconds": self.duration_seconds,
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "summary": self.summary,
        }


def not_invoked_agent_stats() -> AgentPhaseStats:
    return AgentPhaseStats(
        invoked=False,
        duration_seconds="N/A",
        cost_usd="N/A",
        input_tokens="N/A",
        output_tokens="N/A",
        summary="N/A",
    )


def _invoked_agent_stats(
    duration_seconds: float,
    cost_usd: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    summary: str | None,
) -> AgentPhaseStats:
    return AgentPhaseStats(
        invoked=True,
        duration_seconds=duration_seconds,
        cost_usd=cost_usd if cost_usd is not None else "N/A",
        input_tokens=input_tokens if input_tokens is not None else "N/A",
        output_tokens=output_tokens if output_tokens is not None else "N/A",
        summary=summary or "unavailable",
    )


def agent_phase_stats_from_build(result: BuildExplorationResult) -> AgentPhaseStats:
    if not result.llm_used:
        return not_invoked_agent_stats()
    return _invoked_agent_stats(
        result.duration_seconds,
        result.cost_usd,
        result.input_tokens,
        result.output_tokens,
        result.agent_summary,
    )


def agent_phase_stats_from_harness(result: HarnessExplorationResult) -> AgentPhaseStats:
    if not result.llm_used:
        return not_invoked_agent_stats()
    return _invoked_agent_stats(
        result.duration_seconds,
        result.cost_usd,
        result.input_tokens,
        result.output_tokens,
        result.agent_summary,
    )


def agent_phase_stats_from_agent_error(
    summary: AgentRunSummary, report: AgentReport | None
) -> AgentPhaseStats:
    return _invoked_agent_stats(
        summary.duration_seconds,
        summary.cost_usd,
        summary.input_tokens,
        summary.output_tokens,
        report.summary if report else None,
    )


@dataclass
class RunStats:
    total_duration_seconds: float
    library_build_agent: AgentPhaseStats
    harness_build_agent: AgentPhaseStats
    status: RunStatus

    def to_dict(self) -> dict[str, object]:
        return {
            "total_duration_seconds": self.total_duration_seconds,
            "library_build_agent": self.library_build_agent.to_dict(),
            "harness_build_agent": self.harness_build_agent.to_dict(),
            "status": self.status.value,
        }


def write_run_stats(path: Path, stats: RunStats) -> None:
    path.write_text(json.dumps(stats.to_dict(), indent=2))
