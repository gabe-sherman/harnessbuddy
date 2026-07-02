from __future__ import annotations

import json
from pathlib import Path

from harnessbuddy.core.agent_stream import AgentRunSummary
from harnessbuddy.library_builder.models import (
    AgentReport,
    BuildExplorationResult,
    BuildSystem,
    HarnessExplorationResult,
)
from harnessbuddy.library_builder.stats import (
    AgentPhaseStats,
    RunStats,
    RunStatus,
    agent_phase_stats_from_agent_error,
    agent_phase_stats_from_build,
    agent_phase_stats_from_harness,
    not_invoked_agent_stats,
    write_run_stats,
)


def _build_result(
    *,
    llm_used: bool,
    duration_seconds: float = 1.0,
    cost_usd: float | None = None,
    agent_summary: str | None = None,
) -> BuildExplorationResult:
    return BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=duration_seconds,
        llm_used=llm_used,
        cost_usd=cost_usd,
        agent_summary=agent_summary,
    )


def _harness_result(
    *,
    llm_used: bool,
    duration_seconds: float = 1.0,
    cost_usd: float | None = None,
    agent_summary: str | None = None,
) -> HarnessExplorationResult:
    return HarnessExplorationResult(
        succeeded=True,
        command=[],
        static_libs=[],
        include_dir=Path("/tmp/install/include"),
        transitive_link_flags=[],
        stdout="",
        stderr="",
        exit_code=0,
        llm_used=llm_used,
        duration_seconds=duration_seconds,
        cost_usd=cost_usd,
        agent_summary=agent_summary,
    )


# --- not_invoked_agent_stats ---


def test_not_invoked_agent_stats_reports_na_everywhere() -> None:
    stats = not_invoked_agent_stats()
    assert stats.invoked is False
    assert stats.duration_seconds == "N/A"
    assert stats.cost_usd == "N/A"
    assert stats.summary == "N/A"


# --- agent_phase_stats_from_build ---


def test_agent_phase_stats_from_build_not_invoked() -> None:
    result = _build_result(llm_used=False)
    stats = agent_phase_stats_from_build(result)
    assert stats == not_invoked_agent_stats()


def test_agent_phase_stats_from_build_invoked_with_real_values() -> None:
    result = _build_result(
        llm_used=True, duration_seconds=12.5, cost_usd=0.05, agent_summary="Added a CMake flag."
    )
    stats = agent_phase_stats_from_build(result)
    assert stats.invoked is True
    assert stats.duration_seconds == 12.5
    assert stats.cost_usd == 0.05
    assert stats.summary == "Added a CMake flag."


def test_agent_phase_stats_from_build_invoked_no_cost_is_na() -> None:
    result = _build_result(
        llm_used=True, duration_seconds=8.0, cost_usd=None, agent_summary="hello world"
    )
    stats = agent_phase_stats_from_build(result)
    assert stats.cost_usd == "N/A"


def test_agent_phase_stats_from_build_invoked_no_summary_is_unavailable() -> None:
    result = _build_result(llm_used=True, duration_seconds=8.0, cost_usd=0.01, agent_summary=None)
    stats = agent_phase_stats_from_build(result)
    assert stats.summary == "unavailable"


# --- agent_phase_stats_from_harness ---


def test_agent_phase_stats_from_harness_not_invoked() -> None:
    result = _harness_result(llm_used=False)
    stats = agent_phase_stats_from_harness(result)
    assert stats == not_invoked_agent_stats()


def test_agent_phase_stats_from_harness_invoked_with_real_values() -> None:
    result = _harness_result(
        llm_used=True,
        duration_seconds=71.8,
        cost_usd=0.0913,
        agent_summary="Fixed the link flags.",
    )
    stats = agent_phase_stats_from_harness(result)
    assert stats.invoked is True
    assert stats.duration_seconds == 71.8
    assert stats.cost_usd == 0.0913
    assert stats.summary == "Fixed the link flags."


def test_agent_phase_stats_from_harness_invoked_no_cost_is_na() -> None:
    result = _harness_result(
        llm_used=True, duration_seconds=88.2, cost_usd=None, agent_summary="hello world"
    )
    stats = agent_phase_stats_from_harness(result)
    assert stats.cost_usd == "N/A"


def test_agent_phase_stats_from_harness_invoked_no_summary_is_unavailable() -> None:
    result = _harness_result(
        llm_used=True, duration_seconds=88.2, cost_usd=None, agent_summary=None
    )
    stats = agent_phase_stats_from_harness(result)
    assert stats.summary == "unavailable"


# --- agent_phase_stats_from_agent_error ---


def test_agent_phase_stats_from_agent_error_real_values() -> None:
    summary = AgentRunSummary(
        backend="claude",
        outcome="failed",
        duration_seconds=42.0,
        cost_usd=0.02,
    )
    report = AgentReport(summary="Could not resolve the missing package.")
    stats = agent_phase_stats_from_agent_error(summary, report)
    assert stats.invoked is True
    assert stats.duration_seconds == 42.0
    assert stats.cost_usd == 0.02
    assert stats.summary == "Could not resolve the missing package."


def test_agent_phase_stats_from_agent_error_no_cost_is_na() -> None:
    summary = AgentRunSummary(
        backend="codex",
        outcome="failed",
        duration_seconds=42.0,
        cost_usd=None,
    )
    report = AgentReport(summary="hello world")
    stats = agent_phase_stats_from_agent_error(summary, report)
    assert stats.cost_usd == "N/A"


def test_agent_phase_stats_from_agent_error_no_report_is_unavailable() -> None:
    summary = AgentRunSummary(
        backend="claude",
        outcome="timed_out",
        duration_seconds=42.0,
        cost_usd=None,
    )
    stats = agent_phase_stats_from_agent_error(summary, None)
    assert stats.summary == "unavailable"


def test_agent_phase_stats_from_agent_error_report_without_summary_is_unavailable() -> None:
    summary = AgentRunSummary(
        backend="claude",
        outcome="timed_out",
        duration_seconds=42.0,
        cost_usd=None,
    )
    report = AgentReport(summary=None)
    stats = agent_phase_stats_from_agent_error(summary, report)
    assert stats.summary == "unavailable"


# --- RunStats / write_run_stats (worked examples from contracts/stats-json.md) ---


def test_write_run_stats_clean_success(tmp_path: Path) -> None:
    stats = RunStats(
        total_duration_seconds=12.4,
        library_build_agent=not_invoked_agent_stats(),
        harness_build_agent=not_invoked_agent_stats(),
        status=RunStatus.SUCCESS,
    )
    path = tmp_path / "stats.json"
    write_run_stats(path, stats)
    assert json.loads(path.read_text()) == {
        "total_duration_seconds": 12.4,
        "library_build_agent": {
            "invoked": False,
            "duration_seconds": "N/A",
            "cost_usd": "N/A",
            "input_tokens": "N/A",
            "output_tokens": "N/A",
            "summary": "N/A",
        },
        "harness_build_agent": {
            "invoked": False,
            "duration_seconds": "N/A",
            "cost_usd": "N/A",
            "input_tokens": "N/A",
            "output_tokens": "N/A",
            "summary": "N/A",
        },
        "status": "success",
    }


def test_write_run_stats_library_agent_repaired(tmp_path: Path) -> None:
    stats = RunStats(
        total_duration_seconds=96.1,
        library_build_agent=AgentPhaseStats(
            invoked=True,
            duration_seconds=71.8,
            cost_usd=0.0913,
            input_tokens=1220,
            output_tokens=3400,
            summary=(
                "Added -DBUILD_SHARED_LIBS=OFF to the CMake invocation; "
                "install/lib/libfoo.a is now produced."
            ),
        ),
        harness_build_agent=not_invoked_agent_stats(),
        status=RunStatus.SUCCESS,
    )
    path = tmp_path / "stats.json"
    write_run_stats(path, stats)
    assert json.loads(path.read_text()) == {
        "total_duration_seconds": 96.1,
        "library_build_agent": {
            "invoked": True,
            "duration_seconds": 71.8,
            "cost_usd": 0.0913,
            "input_tokens": 1220,
            "output_tokens": 3400,
            "summary": (
                "Added -DBUILD_SHARED_LIBS=OFF to the CMake invocation; "
                "install/lib/libfoo.a is now produced."
            ),
        },
        "harness_build_agent": {
            "invoked": False,
            "duration_seconds": "N/A",
            "cost_usd": "N/A",
            "input_tokens": "N/A",
            "output_tokens": "N/A",
            "summary": "N/A",
        },
        "status": "success",
    }


def test_write_run_stats_harness_unrecoverable_with_codex(tmp_path: Path) -> None:
    stats = RunStats(
        total_duration_seconds=143.5,
        library_build_agent=not_invoked_agent_stats(),
        harness_build_agent=AgentPhaseStats(
            invoked=True,
            duration_seconds=88.2,
            cost_usd="N/A",
            input_tokens="N/A",
            output_tokens="N/A",
            summary="unavailable",
        ),
        status=RunStatus.FAILED_HARNESS_BUILD,
    )
    path = tmp_path / "stats.json"
    write_run_stats(path, stats)
    assert json.loads(path.read_text()) == {
        "total_duration_seconds": 143.5,
        "library_build_agent": {
            "invoked": False,
            "duration_seconds": "N/A",
            "cost_usd": "N/A",
            "input_tokens": "N/A",
            "output_tokens": "N/A",
            "summary": "N/A",
        },
        "harness_build_agent": {
            "invoked": True,
            "duration_seconds": 88.2,
            "cost_usd": "N/A",
            "input_tokens": "N/A",
            "output_tokens": "N/A",
            "summary": "unavailable",
        },
        "status": "failed_harness_build",
    }
