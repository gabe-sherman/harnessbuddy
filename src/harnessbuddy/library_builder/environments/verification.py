"""The build gate: one script that decides whether a build passed, and where to run it.

`agents/scripts/check_build.sh` is the only definition of "the build passed". This module
decides only where it runs — as a host subprocess, or inside the workspace's own OSS-Fuzz
image with the workspace mounted. The pipeline and every repair agent go through here, so an
agent verifies its fix against the same assertions the pipeline applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harnessbuddy.core.resources import agent_script
from harnessbuddy.core.subprocesses import MergedOutput, run_command_streaming
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.timeouts import DEFAULT_BUILD_TIMEOUT_SECONDS


@dataclass
class VerificationResult(MergedOutput):
    """The atomic pass/fail outcome of the shared check_build.sh script."""

    passed: bool
    command: list[str]
    stdout: str
    stderr: str
    duration_seconds: float


def verification_command(
    workspace: Path, *, environment: Environment, project_name: str
) -> list[str]:
    """The gate invocation for workspace, without running it.

    Shared by run_verification and by callers that already know the answer — a probe that
    failed — and only need a command to report as "reproduce with".

    The path is resolved because every gate script cds to the workspace it is handed while
    _run also sets cwd to it, so a relative path would be applied twice. It also makes the
    reported command runnable from any directory.
    """
    workspace = workspace.resolve()
    if environment is Environment.OSS_FUZZ:
        return [
            "bash",
            str(agent_script("check_build_in_container.sh")),
            str(workspace),
            project_name,
        ]
    return ["bash", str(agent_script("check_build.sh")), str(workspace)]


def run_verification(
    workspace: Path,
    *,
    environment: Environment,
    project_name: str,
    timeout: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
) -> VerificationResult:
    """Run the gate for workspace in the given environment."""
    command = verification_command(workspace, environment=environment, project_name=project_name)
    return _run(command, cwd=workspace.resolve(), timeout=timeout)


def run_from_scratch_docker_verification(
    project_dir: Path,
    *,
    project_name: str,
    timeout: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
) -> VerificationResult:
    """Build project_dir's Dockerfile with nothing mounted and run OSS-Fuzz's `compile`.

    The gate mounts the workspace, which is what puts the artifacts on the host — and also
    what lets a broken clone or apt layer pass unnoticed, since the mount supplies what the
    image failed to. This check keeps the mounted gate honest, so it runs once per successful
    oss-fuzz run, immediately before generation.

    project_dir is resolved for the same reason verification_command resolves it.
    """
    project_dir = project_dir.resolve()
    command = [
        "bash",
        str(agent_script("check_dockerfile_from_scratch.sh")),
        str(project_dir),
        project_name,
    ]
    return _run(command, cwd=project_dir, timeout=timeout)


def _run(command: list[str], *, cwd: Path, timeout: int) -> VerificationResult:
    result = run_command_streaming(command, cwd, timeout)
    return VerificationResult(
        passed=result.exit_code == 0,
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_seconds=result.duration_seconds,
    )
