from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harnessbuddy.core.subprocesses import run_command_streaming

_AGENTS_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent.parent / "agents" / "scripts"

_DEFAULT_TIMEOUT_SECONDS = 900


@dataclass
class VerificationResult:
    """The atomic pass/fail outcome of the shared check_*_build.sh script (FR-001)."""

    passed: bool
    command: list[str]
    stdout: str
    stderr: str
    duration_seconds: float


def run_docker_verification(
    workspace: Path, project_name: str, *, timeout: int = _DEFAULT_TIMEOUT_SECONDS
) -> VerificationResult:
    """Run agents/scripts/check_docker_build.sh against workspace — the same command
    HarnessBuddy's own pipeline and the repair agent both invoke for the oss-fuzz
    environment (FR-001, FR-002)."""
    script = _AGENTS_SCRIPTS_DIR / "check_docker_build.sh"
    command = ["bash", str(script), str(workspace), project_name]
    result = run_command_streaming(command, workspace, timeout)
    return VerificationResult(
        passed=result.exit_code == 0,
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_seconds=result.duration_seconds,
    )


def run_local_verification(
    workspace: Path, *, timeout: int = _DEFAULT_TIMEOUT_SECONDS
) -> VerificationResult:
    """Run agents/scripts/check_local_build.sh against workspace — the same command
    HarnessBuddy's own pipeline and the repair agent both invoke for the local
    environment (FR-001, FR-003)."""
    script = _AGENTS_SCRIPTS_DIR / "check_local_build.sh"
    command = ["bash", str(script), str(workspace)]
    result = run_command_streaming(command, workspace, timeout)
    return VerificationResult(
        passed=result.exit_code == 0,
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_seconds=result.duration_seconds,
    )
