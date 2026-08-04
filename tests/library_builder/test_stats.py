from __future__ import annotations

import json
from pathlib import Path

from harnessbuddy.library_builder.models import (
    BuildExplorationResult,
    BuildSystem,
    HarnessExplorationResult,
)
from harnessbuddy.library_builder.stats import (
    AgentPhaseStats,
    RunStats,
    RunStatus,
    agent_phase_stats,
    not_invoked_agent_stats,
    write_run_stats,
)

_NOT_INVOKED_JSON = {
    "invoked": False,
    "duration_seconds": None,
    "cost_usd": None,
    "input_tokens": None,
    "output_tokens": None,
    "summary": None,
}


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


def test_not_invoked_agent_stats_leaves_every_value_absent() -> None:
    stats = not_invoked_agent_stats()
    assert stats.invoked is False
    assert stats.duration_seconds is None
    assert stats.cost_usd is None
    assert stats.summary is None


# --- agent_phase_stats ---


def test_agent_phase_stats_library_not_invoked() -> None:
    assert agent_phase_stats(_build_result(llm_used=False)) == not_invoked_agent_stats()


def test_agent_phase_stats_harness_not_invoked() -> None:
    assert agent_phase_stats(_harness_result(llm_used=False)) == not_invoked_agent_stats()


def test_agent_phase_stats_library_invoked_with_real_values() -> None:
    result = _build_result(
        llm_used=True, duration_seconds=12.5, cost_usd=0.05, agent_summary="Added a CMake flag."
    )
    stats = agent_phase_stats(result)
    assert stats.invoked is True
    assert stats.duration_seconds == 12.5
    assert stats.cost_usd == 0.05
    assert stats.summary == "Added a CMake flag."


def test_agent_phase_stats_harness_invoked_with_real_values() -> None:
    result = _harness_result(
        llm_used=True,
        duration_seconds=71.8,
        cost_usd=0.0913,
        agent_summary="Fixed the link flags.",
    )
    stats = agent_phase_stats(result)
    assert stats.invoked is True
    assert stats.duration_seconds == 71.8
    assert stats.cost_usd == 0.0913
    assert stats.summary == "Fixed the link flags."


def test_agent_phase_stats_invoked_without_cost_reports_none() -> None:
    result = _build_result(
        llm_used=True, duration_seconds=8.0, cost_usd=None, agent_summary="hello world"
    )
    assert agent_phase_stats(result).cost_usd is None


def test_agent_phase_stats_invoked_without_summary_reports_none() -> None:
    result = _harness_result(llm_used=True, duration_seconds=8.0, cost_usd=0.01, agent_summary=None)
    stats = agent_phase_stats(result)
    assert stats.invoked is True
    assert stats.summary is None


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
        "library_build_agent": _NOT_INVOKED_JSON,
        "harness_build_agent": _NOT_INVOKED_JSON,
        "status": "success",
        "environment": "local",
        "compile_commands_path": None,
        "verification_command": None,
        "build_parameters": None,
    }


def test_write_run_stats_library_agent_repaired(tmp_path: Path) -> None:
    summary = (
        "Added -DBUILD_SHARED_LIBS=OFF to the CMake invocation; "
        "install/lib/libfoo.a is now produced."
    )
    stats = RunStats(
        total_duration_seconds=96.1,
        library_build_agent=AgentPhaseStats(
            invoked=True,
            duration_seconds=71.8,
            cost_usd=0.0913,
            input_tokens=1220,
            output_tokens=3400,
            summary=summary,
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
            "summary": summary,
        },
        "harness_build_agent": _NOT_INVOKED_JSON,
        "status": "success",
        "environment": "local",
        "compile_commands_path": None,
        "verification_command": None,
        "build_parameters": None,
    }


def test_write_run_stats_harness_agent_ran_but_reported_nothing(tmp_path: Path) -> None:
    """An agent that ran without reporting cost or a summary emits nulls, not sentinels —
    `invoked` is what distinguishes it from an agent that never ran."""
    stats = RunStats(
        total_duration_seconds=143.5,
        library_build_agent=not_invoked_agent_stats(),
        harness_build_agent=AgentPhaseStats(invoked=True, duration_seconds=88.2),
        status=RunStatus.FAILED_HARNESS_BUILD,
    )
    path = tmp_path / "stats.json"
    write_run_stats(path, stats)
    assert json.loads(path.read_text()) == {
        "total_duration_seconds": 143.5,
        "library_build_agent": _NOT_INVOKED_JSON,
        "harness_build_agent": {
            "invoked": True,
            "duration_seconds": 88.2,
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
            "summary": None,
        },
        "status": "failed_harness_build",
        "environment": "local",
        "compile_commands_path": None,
        "verification_command": None,
        "build_parameters": None,
    }


def test_write_run_stats_records_the_configure_options_a_run_used(tmp_path: Path) -> None:
    """stats.json has to describe the configuration that produced the output, and the
    configure options change what gets built."""
    import dataclasses

    from harnessbuddy.library_builder.build_parameters import BuildParameters

    parameters = dataclasses.replace(
        BuildParameters.defaults(), library_configure_args=("-DBUILD_TESTING=OFF",)
    )
    stats = RunStats(
        total_duration_seconds=1.0,
        library_build_agent=not_invoked_agent_stats(),
        harness_build_agent=not_invoked_agent_stats(),
        status=RunStatus.SUCCESS,
        build_parameters=parameters.to_dict(),
    )
    path = tmp_path / "stats.json"
    write_run_stats(path, stats)
    recorded = json.loads(path.read_text())["build_parameters"]
    assert recorded["library_configure_args"] == ["-DBUILD_TESTING=OFF"]
