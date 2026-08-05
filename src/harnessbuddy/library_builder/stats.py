from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import BuildExplorationResult, HarnessExplorationResult


class RunStatus(Enum):
    SUCCESS = "success"
    FAILED_LIBRARY_BUILD = "failed_library_build"
    FAILED_HARNESS_BUILD = "failed_harness_build"
    FAILED_DOCKERFILE_VERIFICATION = "failed_dockerfile_verification"
    FAILED_OUTPUT_GENERATION = "failed_output_generation"


@dataclass
class AgentPhaseStats:
    """One phase's agent accounting, or the record that no agent ran.

    Every numeric field is None when no agent was invoked, so stats.json carries JSON nulls
    rather than an "N/A" string a consumer would have to type-check around. `invoked` is what
    says which case it is.
    """

    invoked: bool
    duration_seconds: float | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    summary: str | None = None

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
    return AgentPhaseStats(invoked=False)


def agent_phase_stats(result: BuildExplorationResult | HarnessExplorationResult) -> AgentPhaseStats:
    """The agent accounting carried by either stage's result.

    One function for both, since both report it through the same AgentOutcome fields.
    """
    if not result.llm_used:
        return not_invoked_agent_stats()
    return AgentPhaseStats(
        invoked=True,
        duration_seconds=result.duration_seconds,
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        summary=result.agent_summary,
    )


@dataclass
class RunStats:
    total_duration_seconds: float
    library_build_agent: AgentPhaseStats
    harness_build_agent: AgentPhaseStats
    status: RunStatus
    environment: Environment = Environment.LOCAL
    compile_commands_path: str | None = None
    # The literal command the gate was invoked with, so a person can reproduce the result.
    verification_command: str | None = None
    build_parameters: dict[str, str | list[str]] | None = None
    # True when --bypass-scratch-validation ran: no from-scratch rebuild proved this result.
    scratch_validation_bypassed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "total_duration_seconds": self.total_duration_seconds,
            "library_build_agent": self.library_build_agent.to_dict(),
            "harness_build_agent": self.harness_build_agent.to_dict(),
            "status": self.status.value,
            "environment": self.environment.value,
            "compile_commands_path": self.compile_commands_path,
            "verification_command": self.verification_command,
            "build_parameters": self.build_parameters,
            "scratch_validation_bypassed": self.scratch_validation_bypassed,
        }


def write_run_stats(path: Path, stats: RunStats) -> None:
    path.write_text(json.dumps(stats.to_dict(), indent=2))
