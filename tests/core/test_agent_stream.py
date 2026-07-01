from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.agent_stream import (
    AgentRunSummary,
    _claude_result_cost,
    _codex_turn_completed_usage,
    _parse_claude_line,
    _parse_codex_line,
    run_agent_streaming,
    write_agent_report,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "agent_streams"


def _lines(name: str) -> list[str]:
    return (_FIXTURES / name).read_text().splitlines()


def _claude_lines() -> list[str]:
    return _lines("claude_stream_sample.jsonl")


def _codex_lines() -> list[str]:
    return _lines("codex_stream_sample.jsonl")


def _malformed_lines() -> list[str]:
    return _lines("malformed_stream_sample.jsonl")


# --- Claude ---


def test_parse_claude_line_text_block_is_status() -> None:
    events = [e for line in _claude_lines() for e in _parse_claude_line(line)]
    status_events = [e for e in events if e.kind == "status"]
    assert any("failing build script" in e.text for e in status_events)


def test_parse_claude_line_read_tool_use_is_file_read() -> None:
    events = [e for line in _claude_lines() for e in _parse_claude_line(line)]
    file_reads = [e for e in events if e.kind == "file_read"]
    assert len(file_reads) == 1
    assert "build_library.sh" in file_reads[0].text


def test_parse_claude_line_edit_tool_use_is_file_edit() -> None:
    events = [e for line in _claude_lines() for e in _parse_claude_line(line)]
    file_edits = [e for e in events if e.kind == "file_edit"]
    assert len(file_edits) == 1
    assert "build_library.sh" in file_edits[0].text


def test_parse_claude_line_bash_tool_use_is_command_run() -> None:
    events = [e for line in _claude_lines() for e in _parse_claude_line(line)]
    command_runs = [e for e in events if e.kind == "command_run"]
    assert len(command_runs) == 1
    assert "bash build_library.sh" in command_runs[0].text


def test_parse_claude_line_system_and_result_emit_nothing() -> None:
    lines = _claude_lines()
    system_events = _parse_claude_line(lines[0])
    result_events = _parse_claude_line(lines[-1])
    assert system_events == []
    assert result_events == []


def test_parse_claude_line_malformed_json_is_raw_fallback() -> None:
    line = _malformed_lines()[0]
    events = _parse_claude_line(line)
    assert len(events) == 1
    assert events[0].kind == "raw_fallback"
    assert events[0].text == line.rstrip("\n")


def test_parse_claude_line_unrecognized_shape_is_raw_fallback() -> None:
    line = _malformed_lines()[1]
    events = _parse_claude_line(line)
    assert len(events) == 1
    assert events[0].kind == "raw_fallback"
    assert events[0].text == line.rstrip("\n")


# --- Codex ---


def test_parse_codex_line_command_execution_is_command_run() -> None:
    events = [e for line in _codex_lines() for e in _parse_codex_line(line)]
    command_runs = [e for e in events if e.kind == "command_run"]
    assert len(command_runs) >= 1
    assert any("bash build_library.sh" in e.text for e in command_runs)


def test_parse_codex_line_file_change_is_file_edit() -> None:
    events = [e for line in _codex_lines() for e in _parse_codex_line(line)]
    file_edits = [e for e in events if e.kind == "file_edit"]
    assert len(file_edits) == 1
    assert "build_library.sh" in file_edits[0].text


def test_parse_codex_line_thread_and_turn_events_emit_nothing() -> None:
    lines = _codex_lines()
    assert _parse_codex_line(lines[0]) == []  # thread.started
    assert _parse_codex_line(lines[1]) == []  # turn.started
    assert _parse_codex_line(lines[-1]) == []  # turn.completed


def test_parse_codex_line_malformed_json_is_raw_fallback() -> None:
    line = _malformed_lines()[0]
    events = _parse_codex_line(line)
    assert len(events) == 1
    assert events[0].kind == "raw_fallback"
    assert events[0].text == line.rstrip("\n")


def test_parse_codex_line_unrecognized_shape_is_raw_fallback() -> None:
    line = _malformed_lines()[1]
    events = _parse_codex_line(line)
    assert len(events) == 1
    assert events[0].kind == "raw_fallback"
    assert events[0].text == line.rstrip("\n")


# --- Stats extraction (US2) ---


def test_parse_claude_result_line_extracts_cost() -> None:
    result_line = _claude_lines()[-1]
    assert _claude_result_cost(result_line) == 0.1234


class _FakeProcess:
    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self.returncode

    def kill(self) -> None:
        pass


def test_codex_turn_completed_usage_extraction() -> None:
    turn_completed_line = _codex_lines()[-1]
    input_tokens, output_tokens = _codex_turn_completed_usage(turn_completed_line)
    assert input_tokens == 5230
    assert output_tokens == 941


def test_codex_stream_extracts_token_usage_and_no_cost(tmp_path: Path) -> None:
    fake_proc = _FakeProcess([line + "\n" for line in _codex_lines()])
    with patch("harnessbuddy.core.agent_stream.subprocess.Popen", return_value=fake_proc):
        result = run_agent_streaming(["codex", "exec"], tmp_path, 60, "codex")
    assert result.cost_usd is None
    assert result.input_tokens == 5230
    assert result.output_tokens == 941


# --- write_agent_report (US2) ---


def test_write_agent_report_cost_trailer(tmp_path: Path) -> None:
    path = tmp_path / "report.log"
    write_agent_report(
        path,
        "transcript text",
        AgentRunSummary(
            backend="claude", outcome="succeeded", duration_seconds=12.3, cost_usd=0.4567
        ),
    )
    content = path.read_text()
    assert content.startswith("transcript text")
    assert "=== Agent Run Summary ===" in content
    assert "backend: claude" in content
    assert "outcome: succeeded" in content
    assert content.rstrip("\n").endswith("cost: $0.4567")


def test_write_agent_report_tokens_trailer(tmp_path: Path) -> None:
    path = tmp_path / "report.log"
    write_agent_report(
        path,
        "transcript text",
        AgentRunSummary(
            backend="codex",
            outcome="succeeded",
            duration_seconds=5.0,
            cost_usd=None,
            input_tokens=100,
            output_tokens=50,
        ),
    )
    content = path.read_text()
    assert content.rstrip("\n").endswith("tokens: input=100 output=50")


def test_write_agent_report_unavailable_trailer(tmp_path: Path) -> None:
    path = tmp_path / "report.log"
    write_agent_report(
        path,
        "transcript text",
        AgentRunSummary(backend="claude", outcome="failed", duration_seconds=1.0, cost_usd=None),
    )
    content = path.read_text()
    assert content.rstrip("\n").endswith("cost: unavailable")
