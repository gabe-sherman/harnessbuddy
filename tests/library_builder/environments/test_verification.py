"""The gate is one script; this module only decides where it runs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.environments.verification import (
    run_from_scratch_docker_verification,
    run_verification,
    verification_command,
)

_PASSED = RunResult(stdout="OK", stderr="", exit_code=0, duration_seconds=0.1)


def _patched_runner(result: RunResult = _PASSED):  # type: ignore[no-untyped-def]
    return patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=result,
    )


@pytest.mark.parametrize("environment", list(Environment))
def test_command_builder_matches_what_run_verification_executes(
    environment: Environment, tmp_path: Path
) -> None:
    """The pure command builder must stay in sync with what actually runs, since callers use
    it to report a reproduce-it command without paying to run it."""
    with _patched_runner() as mock_run:
        result = run_verification(tmp_path, environment=environment, project_name="mylib")

    expected = verification_command(tmp_path, environment=environment, project_name="mylib")
    assert expected == mock_run.call_args[0][0]
    assert expected == result.command


def test_local_verification_runs_the_gate_directly(tmp_path: Path) -> None:
    with _patched_runner() as mock_run:
        run_verification(tmp_path, environment=Environment.LOCAL, project_name="mylib")

    command, cwd, _timeout = mock_run.call_args[0]
    assert command[0] == "bash"
    assert command[1].endswith("check_build.sh")
    assert command[2:] == [str(tmp_path)]
    assert cwd == tmp_path


def test_oss_fuzz_verification_runs_the_gate_in_a_container(tmp_path: Path) -> None:
    """Same assertions, different place: the container wrapper builds the image and mounts
    the workspace, then runs the one gate script inside it."""
    with _patched_runner() as mock_run:
        run_verification(tmp_path, environment=Environment.OSS_FUZZ, project_name="mylib")

    command, _cwd, _timeout = mock_run.call_args[0]
    assert command[1].endswith("check_build_in_container.sh")
    assert command[2:] == [str(tmp_path), "mylib"]


def test_verification_result_carries_the_subprocess_output(tmp_path: Path) -> None:
    with _patched_runner(
        RunResult(stdout="OK: artifacts present", stderr="", exit_code=0, duration_seconds=1.5)
    ):
        result = run_verification(tmp_path, environment=Environment.LOCAL, project_name="mylib")

    assert result.passed is True
    assert result.stdout == "OK: artifacts present"
    assert result.duration_seconds == 1.5


def test_nonzero_exit_is_not_passed(tmp_path: Path) -> None:
    with _patched_runner(
        RunResult(
            stdout="FAILED: no static libraries", stderr="", exit_code=1, duration_seconds=0.5
        )
    ):
        result = run_verification(tmp_path, environment=Environment.LOCAL, project_name="mylib")

    assert result.passed is False


def test_from_scratch_docker_verification_argv(tmp_path: Path) -> None:
    with _patched_runner() as mock_run:
        result = run_from_scratch_docker_verification(tmp_path, project_name="mylib")

    command, cwd, _timeout = mock_run.call_args[0]
    assert command[1].endswith("check_dockerfile_from_scratch.sh")
    assert command[2:] == [str(tmp_path), "mylib"]
    assert cwd == tmp_path
    assert result.passed is True


@pytest.mark.parametrize(
    "run_gate",
    [
        pytest.param(
            lambda p: run_verification(p, environment=Environment.OSS_FUZZ, project_name="mylib"),
            id="mounted-gate",
        ),
        pytest.param(
            lambda p: run_from_scratch_docker_verification(p, project_name="mylib"),
            id="from-scratch",
        ),
    ],
)
def test_a_relative_workspace_is_resolved_before_the_gate_sees_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_gate,  # type: ignore[no-untyped-def]
) -> None:
    """Every gate script cds to the path it is handed, and _run also sets cwd to that path, so
    a relative one would be applied twice — `cd .harnessbuddy/foo` from inside
    .harnessbuddy/foo. The CLI passes exactly such a path (paths.default_state_dir is
    relative), which is how this reached a real run as
    `cd: .harnessbuddy/<project>: No such file or directory`."""
    workspace = tmp_path / ".harnessbuddy" / "myproject"
    workspace.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    with _patched_runner() as mock_run:
        run_gate(Path(".harnessbuddy/myproject"))

    command, cwd, _timeout = mock_run.call_args[0]
    assert cwd == workspace
    assert str(workspace) in command


def test_environment_unavailable_pattern_matches_gate_output(tmp_path: Path) -> None:
    """The gate's own `docker build` writes daemon-unreachable errors into the merged output
    stream, so callers distinguishing an unavailable environment from a real build failure
    read `output`, not `stderr` — which run_command_streaming always leaves empty."""
    from harnessbuddy.library_builder.environments.oss_fuzz import _is_environment_unavailable

    with _patched_runner(
        RunResult(
            stdout="FAILED: docker build failed for .\nError response from daemon: ...",
            stderr="",
            exit_code=1,
            duration_seconds=0.1,
        )
    ):
        result = run_verification(tmp_path, environment=Environment.OSS_FUZZ, project_name="mylib")

    assert _is_environment_unavailable(result.output)
