from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.sandbox import sandbox_test


def _run_result(exit_code: int = 0, stdout: str = "", stderr: str = "") -> RunResult:
    return RunResult(stdout=stdout, stderr=stderr, exit_code=exit_code, duration_seconds=0.5)


class TestSandboxTest:
    def test_skipped_when_docker_not_found(self, tmp_path: Path) -> None:
        with patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=False):
            result = sandbox_test(tmp_path)

        assert result.skipped is True
        assert result.succeeded is False

    def test_skip_reason_mentions_docker(self, tmp_path: Path) -> None:
        with patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=False):
            result = sandbox_test(tmp_path)

        assert "docker" in result.skip_reason.lower()

    def test_skipped_result_has_zero_duration(self, tmp_path: Path) -> None:
        with patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=False):
            result = sandbox_test(tmp_path)

        assert result.duration_seconds == 0.0

    def test_succeeded_when_exit_code_zero(self, tmp_path: Path) -> None:
        with (
            patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=True),
            patch(
                "harnessbuddy.library_builder.sandbox.run_command",
                return_value=_run_result(exit_code=0),
            ),
        ):
            result = sandbox_test(tmp_path)

        assert result.succeeded is True
        assert result.skipped is False

    def test_failed_when_exit_code_nonzero(self, tmp_path: Path) -> None:
        with (
            patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=True),
            patch(
                "harnessbuddy.library_builder.sandbox.run_command",
                return_value=_run_result(exit_code=1),
            ),
        ):
            result = sandbox_test(tmp_path)

        assert result.succeeded is False
        assert result.skipped is False

    def test_command_includes_no_cache(self, tmp_path: Path) -> None:
        with (
            patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=True),
            patch(
                "harnessbuddy.library_builder.sandbox.run_command",
                return_value=_run_result(),
            ) as mock_run,
        ):
            sandbox_test(tmp_path)

        cmd = mock_run.call_args[0][0]
        assert "--no-cache" in cmd

    def test_command_includes_docker_build(self, tmp_path: Path) -> None:
        with (
            patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=True),
            patch(
                "harnessbuddy.library_builder.sandbox.run_command",
                return_value=_run_result(),
            ) as mock_run,
        ):
            sandbox_test(tmp_path)

        cmd = mock_run.call_args[0][0]
        assert "docker" in cmd
        assert "build" in cmd

    def test_cwd_is_output_path(self, tmp_path: Path) -> None:
        with (
            patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=True),
            patch(
                "harnessbuddy.library_builder.sandbox.run_command",
                return_value=_run_result(),
            ) as mock_run,
        ):
            sandbox_test(tmp_path)

        kwargs = mock_run.call_args[1]
        assert kwargs["cwd"] == tmp_path

    def test_stderr_captured_in_result(self, tmp_path: Path) -> None:
        with (
            patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=True),
            patch(
                "harnessbuddy.library_builder.sandbox.run_command",
                return_value=_run_result(exit_code=1, stderr="step failed: layer error"),
            ),
        ):
            result = sandbox_test(tmp_path)

        assert "step failed" in result.stderr

    def test_timeout_forwarded(self, tmp_path: Path) -> None:
        with (
            patch("harnessbuddy.library_builder.sandbox._docker_available", return_value=True),
            patch(
                "harnessbuddy.library_builder.sandbox.run_command",
                return_value=_run_result(),
            ) as mock_run,
        ):
            sandbox_test(tmp_path, timeout=42)

        kwargs = mock_run.call_args[1]
        assert kwargs["timeout"] == 42
