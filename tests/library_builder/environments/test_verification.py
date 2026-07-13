from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.verification import (
    docker_verification_command,
    local_verification_command,
    run_docker_verification,
    run_local_verification,
)


def test_local_verification_command_matches_what_run_local_verification_executes(
    tmp_path: Path,
) -> None:
    """The pure command builder must stay in sync with what actually runs, since
    callers use it to report a reproduce-it command without paying to run it."""
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=RunResult(stdout="OK", stderr="", exit_code=0, duration_seconds=0.1),
    ) as mock_run:
        result = run_local_verification(tmp_path)

    assert local_verification_command(tmp_path) == mock_run.call_args[0][0]
    assert local_verification_command(tmp_path) == result.command


def test_docker_verification_command_matches_what_run_docker_verification_executes(
    tmp_path: Path,
) -> None:
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=RunResult(stdout="OK", stderr="", exit_code=0, duration_seconds=0.1),
    ) as mock_run:
        result = run_docker_verification(tmp_path, "mylib")

    assert docker_verification_command(tmp_path, "mylib") == mock_run.call_args[0][0]
    assert docker_verification_command(tmp_path, "mylib") == result.command


def test_run_local_verification_argv(tmp_path: Path) -> None:
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=RunResult(stdout="OK", stderr="", exit_code=0, duration_seconds=0.5),
    ) as mock_run:
        result = run_local_verification(tmp_path)

    (command, cwd, _timeout) = mock_run.call_args[0]
    assert command == ["bash", command[1], str(tmp_path)]
    assert command[1].endswith("check_local_build.sh")
    assert cwd == tmp_path
    assert result.command == command


def test_run_local_verification_populates_result_from_subprocess(tmp_path: Path) -> None:
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=RunResult(
            stdout="OK: artifacts present", stderr="", exit_code=0, duration_seconds=1.5
        ),
    ):
        result = run_local_verification(tmp_path)

    assert result.passed is True
    assert result.stdout == "OK: artifacts present"
    assert result.stderr == ""
    assert result.duration_seconds == 1.5


def test_run_local_verification_failure_is_not_passed(tmp_path: Path) -> None:
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=RunResult(
            stdout="FAILED: build_library.sh did not succeed",
            stderr="",
            exit_code=1,
            duration_seconds=0.5,
        ),
    ):
        result = run_local_verification(tmp_path)

    assert result.passed is False


def test_run_docker_verification_argv(tmp_path: Path) -> None:
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=RunResult(stdout="OK", stderr="", exit_code=0, duration_seconds=2.0),
    ) as mock_run:
        result = run_docker_verification(tmp_path, "mylib")

    (command, cwd, _timeout) = mock_run.call_args[0]
    assert command == ["bash", command[1], str(tmp_path), "mylib"]
    assert command[1].endswith("check_docker_build.sh")
    assert cwd == tmp_path
    assert result.command == command


def test_run_docker_verification_populates_result_from_subprocess(tmp_path: Path) -> None:
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=RunResult(
            stdout="OK: docker build and in-container compile succeeded",
            stderr="",
            exit_code=0,
            duration_seconds=42.0,
        ),
    ):
        result = run_docker_verification(tmp_path, "mylib")

    assert result.passed is True
    assert result.duration_seconds == 42.0


def test_environment_unavailable_pattern_matches_docker_verification_stdout(tmp_path: Path) -> None:
    """check_docker_build.sh's own subprocess (`docker build`) writes daemon-unreachable
    errors that flow through run_command_streaming's merged stdout — callers distinguishing
    environment-unavailable failures from real build failures must check stdout, since
    run_command_streaming always leaves stderr empty (FR-007)."""
    from harnessbuddy.library_builder.environments.oss_fuzz import _is_environment_unavailable

    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=RunResult(
            stdout="FAILED: docker build failed for .\nError response from daemon: ...",
            stderr="",
            exit_code=1,
            duration_seconds=0.1,
        ),
    ):
        result = run_docker_verification(tmp_path, "mylib")

    assert _is_environment_unavailable(result.stdout + result.stderr)
