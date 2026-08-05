from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harnessbuddy.core.subprocesses import run_command_streaming, streaming_context

_ECHO_COMMAND = [sys.executable, "-c", "print('hello'); print('world')"]


def test_run_command_streaming_prints_live_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = run_command_streaming(_ECHO_COMMAND, tmp_path, 10)
    out = capsys.readouterr().out
    assert "hello" in out
    assert "world" in out
    assert result.exit_code == 0


def test_streaming_context_quiet_suppresses_live_printing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with streaming_context(quiet=True):
        result = run_command_streaming(_ECHO_COMMAND, tmp_path, 10)
    out = capsys.readouterr().out
    assert "hello" not in out
    assert "world" not in out
    assert result.exit_code == 0


def test_streaming_context_always_writes_log_regardless_of_quiet(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "deterministic_library_build.log"
    with streaming_context(quiet=True, log_path=log_path):
        run_command_streaming(_ECHO_COMMAND, tmp_path, 10)
    assert log_path.exists()
    content = log_path.read_text()
    assert "hello" in content
    assert "world" in content


def test_streaming_context_writes_log_when_not_quiet_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "logs" / "deterministic_library_build.log"
    with streaming_context(quiet=False, log_path=log_path):
        run_command_streaming(_ECHO_COMMAND, tmp_path, 10)
    out = capsys.readouterr().out
    assert "hello" in out
    assert log_path.exists()
    assert "hello" in log_path.read_text()


def test_streaming_context_resets_after_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with streaming_context(quiet=True):
        pass
    # Outside the context, live printing is restored.
    run_command_streaming(_ECHO_COMMAND, tmp_path, 10)
    out = capsys.readouterr().out
    assert "hello" in out


def test_run_command_streaming_writes_log_on_failed_command(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "deterministic_library_build.log"
    fail_command = [sys.executable, "-c", "print('boom'); import sys; sys.exit(1)"]
    with streaming_context(quiet=True, log_path=log_path):
        result = run_command_streaming(fail_command, tmp_path, 10)
    assert result.exit_code == 1
    assert log_path.exists()
    assert "boom" in log_path.read_text()
