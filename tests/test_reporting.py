from __future__ import annotations

import pytest

from harnessbuddy.core.reporting import (
    FailureDiagnostic,
    Phase,
    PhaseExecution,
    PhaseReporter,
    RunReport,
    build_diagnostic,
    format_diagnostic,
    format_phase_end_banner,
    format_phase_start_banner,
    format_startup_failure,
    is_agent_phase,
    phase_label,
    summarize_message,
)

# Phase


def test_phase_label_matches_data_model() -> None:
    assert phase_label(Phase.INGESTION) == "Repository ingestion"
    assert phase_label(Phase.STATIC_ANALYSIS) == "Static analysis"
    assert phase_label(Phase.STATIC_LIBRARY_BUILD) == "Static library build"
    assert phase_label(Phase.AGENT_LIBRARY_REPAIR) == "Agent-assisted library repair"
    assert phase_label(Phase.HARNESS_COMPILE_PROBE) == "Harness compile probe"
    assert phase_label(Phase.AGENT_HARNESS_REPAIR) == "Agent-assisted harness repair"
    assert phase_label(Phase.OUTPUT_GENERATION) == "Output generation"


def test_is_agent_phase_true_only_for_agent_phases() -> None:
    assert is_agent_phase(Phase.AGENT_LIBRARY_REPAIR)
    assert is_agent_phase(Phase.AGENT_HARNESS_REPAIR)
    assert not is_agent_phase(Phase.STATIC_LIBRARY_BUILD)
    assert not is_agent_phase(Phase.HARNESS_COMPILE_PROBE)
    assert not is_agent_phase(Phase.INGESTION)


# PhaseExecution


def test_phase_execution_starts_running() -> None:
    execution = PhaseExecution(phase=Phase.STATIC_LIBRARY_BUILD, started_at=1.0)
    assert execution.status == "running"
    assert execution.ended_at is None


def test_phase_execution_mark_succeeded_transitions_and_stamps_end_time() -> None:
    execution = PhaseExecution(phase=Phase.STATIC_LIBRARY_BUILD, started_at=1.0)
    execution.mark_succeeded()
    assert execution.status == "succeeded"
    assert execution.ended_at is not None


def test_phase_execution_mark_failed_transitions() -> None:
    execution = PhaseExecution(phase=Phase.STATIC_LIBRARY_BUILD, started_at=1.0)
    execution.mark_failed()
    assert execution.status == "failed"


def test_phase_execution_double_transition_raises() -> None:
    execution = PhaseExecution(phase=Phase.STATIC_LIBRARY_BUILD, started_at=1.0)
    execution.mark_succeeded()
    with pytest.raises(ValueError, match="succeeded"):
        execution.mark_failed()


# RunReport


def test_run_report_add_phase_preserves_order() -> None:
    report = RunReport()
    first = PhaseExecution(phase=Phase.INGESTION, started_at=1.0)
    second = PhaseExecution(phase=Phase.STATIC_ANALYSIS, started_at=2.0)
    report.add_phase(first)
    report.add_phase(second)
    assert report.phases == [first, second]


def test_run_report_add_diagnostic_preserves_order() -> None:
    report = RunReport()
    first = build_diagnostic(
        Phase.STATIC_LIBRARY_BUILD, step="build", message="a", origin="deterministic"
    )
    second = build_diagnostic(
        Phase.HARNESS_COMPILE_PROBE, step="probe", message="b", origin="deterministic"
    )
    report.add_diagnostic(first)
    report.add_diagnostic(second)
    assert report.diagnostics == [first, second]


# summarize_message


def test_summarize_message_returns_last_lines() -> None:
    text = "line1\nline2\nline3\nline4"
    assert summarize_message(text, max_lines=2) == "line3\nline4"


def test_summarize_message_skips_blank_lines() -> None:
    text = "line1\n\n\nline2\n"
    assert summarize_message(text, max_lines=2) == "line1\nline2"


def test_summarize_message_empty_input() -> None:
    assert summarize_message("") == "(no output captured)"
    assert summarize_message("\n\n   \n") == "(no output captured)"


# FailureDiagnostic / build_diagnostic


def test_build_diagnostic_constructs_expected_fields() -> None:
    diagnostic = build_diagnostic(
        Phase.STATIC_LIBRARY_BUILD,
        step="cmake configure",
        message="configure failed",
        origin="deterministic",
        log_path=None,
        exit_code=1,
    )
    assert isinstance(diagnostic, FailureDiagnostic)
    assert diagnostic.phase is Phase.STATIC_LIBRARY_BUILD
    assert diagnostic.step == "cmake configure"
    assert diagnostic.message == "configure failed"
    assert diagnostic.origin == "deterministic"
    assert diagnostic.exit_code == 1


# format_diagnostic


def test_format_diagnostic_contains_required_fields() -> None:
    diagnostic = build_diagnostic(
        Phase.STATIC_LIBRARY_BUILD,
        step="cmake configure",
        message="configure failed",
        origin="deterministic",
        log_path=None,
        exit_code=1,
    )
    rendered = format_diagnostic(diagnostic)
    assert phase_label(Phase.STATIC_LIBRARY_BUILD) in rendered
    assert "cmake configure" in rendered
    assert "configure failed" in rendered
    assert "1" in rendered


def test_format_diagnostic_distinguishes_agent_origin() -> None:
    deterministic = build_diagnostic(
        Phase.STATIC_LIBRARY_BUILD, step="build", message="x", origin="deterministic"
    )
    agent = build_diagnostic(
        Phase.AGENT_LIBRARY_REPAIR, step="repair", message="x", origin="agent"
    )
    deterministic_text = format_diagnostic(deterministic)
    agent_text = format_diagnostic(agent)
    assert deterministic_text != agent_text
    assert "agent" in agent_text.lower()


def test_format_diagnostic_includes_log_path_when_set() -> None:
    from pathlib import Path

    diagnostic = build_diagnostic(
        Phase.STATIC_LIBRARY_BUILD,
        step="build",
        message="x",
        origin="deterministic",
        log_path=Path("/tmp/example.log"),
    )
    assert "/tmp/example.log" in format_diagnostic(diagnostic)


def test_format_diagnostic_omits_raw_output_by_default() -> None:
    diagnostic = build_diagnostic(
        Phase.STATIC_LIBRARY_BUILD, step="build", message="x", origin="deterministic"
    )
    rendered = format_diagnostic(diagnostic, debug=False, raw_output="THE RAW OUTPUT")
    assert "THE RAW OUTPUT" not in rendered


def test_format_diagnostic_includes_raw_output_in_debug_mode() -> None:
    diagnostic = build_diagnostic(
        Phase.STATIC_LIBRARY_BUILD, step="build", message="x", origin="deterministic"
    )
    rendered = format_diagnostic(diagnostic, debug=True, raw_output="THE RAW OUTPUT")
    assert "THE RAW OUTPUT" in rendered


def test_format_diagnostic_debug_without_raw_output_omits_block() -> None:
    diagnostic = build_diagnostic(
        Phase.STATIC_LIBRARY_BUILD, step="build", message="x", origin="deterministic"
    )
    rendered = format_diagnostic(diagnostic, debug=True, raw_output=None)
    assert "raw output" not in rendered.lower()


# format_startup_failure


def test_format_startup_failure_includes_message() -> None:
    rendered = format_startup_failure("environment unavailable")
    assert "environment unavailable" in rendered


# banners


def test_format_phase_start_and_end_banners_differ() -> None:
    start = format_phase_start_banner(Phase.STATIC_LIBRARY_BUILD)
    end = format_phase_end_banner(Phase.STATIC_LIBRARY_BUILD, "succeeded")
    assert start != end
    assert phase_label(Phase.STATIC_LIBRARY_BUILD) in start
    assert phase_label(Phase.STATIC_LIBRARY_BUILD) in end


def test_format_phase_end_banner_distinguishes_success_and_failure() -> None:
    succeeded = format_phase_end_banner(Phase.STATIC_LIBRARY_BUILD, "succeeded")
    failed = format_phase_end_banner(Phase.STATIC_LIBRARY_BUILD, "failed")
    assert succeeded != failed
    assert "SUCCEEDED" in succeeded
    assert "FAILED" in failed


def test_agent_banner_visually_and_textually_distinct_from_deterministic() -> None:
    """FR-002: an agent-assisted phase's banner must differ from a deterministic
    phase's both in wording and in fill character, not wording alone."""
    deterministic = format_phase_start_banner(Phase.STATIC_LIBRARY_BUILD)
    agent = format_phase_start_banner(Phase.AGENT_LIBRARY_REPAIR)
    assert deterministic != agent
    assert "#" in agent
    assert "#" not in deterministic
    assert "AGENT" in agent
    assert "AGENT" not in deterministic


# PhaseReporter


def test_phase_reporter_prints_start_banner_on_enter(capsys: pytest.CaptureFixture[str]) -> None:
    with PhaseReporter(Phase.STATIC_LIBRARY_BUILD) as reporter:
        reporter.succeed()
    out = capsys.readouterr().out
    assert format_phase_start_banner(Phase.STATIC_LIBRARY_BUILD) in out


def test_phase_reporter_succeed_prints_one_end_banner(capsys: pytest.CaptureFixture[str]) -> None:
    with PhaseReporter(Phase.STATIC_LIBRARY_BUILD) as reporter:
        reporter.succeed()
    out = capsys.readouterr().out
    assert out.count(format_phase_end_banner(Phase.STATIC_LIBRARY_BUILD, "succeeded")) == 1
    assert reporter.execution.status == "succeeded"


def test_phase_reporter_fail_prints_one_end_banner(capsys: pytest.CaptureFixture[str]) -> None:
    with PhaseReporter(Phase.STATIC_LIBRARY_BUILD) as reporter:
        reporter.fail()
    out = capsys.readouterr().out
    assert out.count(format_phase_end_banner(Phase.STATIC_LIBRARY_BUILD, "failed")) == 1
    assert reporter.execution.status == "failed"


def test_phase_reporter_exactly_one_start_and_end_line(capsys: pytest.CaptureFixture[str]) -> None:
    with PhaseReporter(Phase.HARNESS_COMPILE_PROBE) as reporter:
        reporter.succeed()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 2


def test_phase_reporter_auto_fails_on_exception(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(RuntimeError), PhaseReporter(Phase.STATIC_LIBRARY_BUILD) as reporter:
        raise RuntimeError("boom")
    out = capsys.readouterr().out
    assert format_phase_end_banner(Phase.STATIC_LIBRARY_BUILD, "failed") in out
    assert reporter.execution.status == "failed"


def test_phase_reporter_does_not_swallow_exception() -> None:
    with pytest.raises(RuntimeError, match="boom"), PhaseReporter(Phase.STATIC_LIBRARY_BUILD):
        raise RuntimeError("boom")


def test_phase_reporter_adds_execution_to_run_report() -> None:
    report = RunReport()
    with PhaseReporter(Phase.STATIC_LIBRARY_BUILD, run_report=report) as reporter:
        reporter.succeed()
    assert report.phases == [reporter.execution]


def test_phase_reporter_set_log_path() -> None:
    from pathlib import Path

    with PhaseReporter(Phase.STATIC_LIBRARY_BUILD) as reporter:
        reporter.set_log_path(Path("/tmp/foo.log"))
        reporter.succeed()
    assert reporter.execution.log_path == Path("/tmp/foo.log")
