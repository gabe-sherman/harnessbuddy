from __future__ import annotations

import shutil
from pathlib import Path

from harnessbuddy.core.subprocesses import run_command
from harnessbuddy.library_builder.models import SandboxResult

_DEFAULT_TIMEOUT = 900  # 15 minutes for docker build


def sandbox_test(output_path: Path, *, timeout: int = _DEFAULT_TIMEOUT) -> SandboxResult:
    """Run docker build in output_path and return the result.

    Returns a skipped result (not a failure) when docker is not on PATH.
    """
    if not _docker_available():
        return SandboxResult(
            succeeded=False,
            skipped=True,
            skip_reason="docker not found on PATH",
            stdout="",
            stderr="",
            exit_code=-1,
            duration_seconds=0.0,
        )

    tag = f"harnessbuddy-test/{output_path.name}"
    result = run_command(
        ["docker", "build", "--no-cache", "-t", tag, "."],
        cwd=output_path,
        timeout=timeout,
    )
    return SandboxResult(
        succeeded=result.exit_code == 0,
        skipped=False,
        skip_reason="",
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
    )


def _docker_available() -> bool:
    return shutil.which("docker") is not None
