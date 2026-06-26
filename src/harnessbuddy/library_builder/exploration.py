from __future__ import annotations

from pathlib import Path

from harnessbuddy.core.subprocesses import run_command
from harnessbuddy.library_builder.models import AnalysisResult, BuildExplorationResult, BuildSystem


def explore(
    analysis: AnalysisResult, workdir: Path, *, timeout: int = 120
) -> BuildExplorationResult:
    build_system = analysis.build_system
    command = _command_for(build_system, workdir)

    if command is None:
        return BuildExplorationResult(
            build_system=build_system,
            succeeded=False,
            command=[],
            stdout="",
            stderr="",
            exit_code=-1,
            duration_seconds=0.0,
        )

    result = run_command(command, analysis.source_path, timeout)

    return BuildExplorationResult(
        build_system=build_system,
        succeeded=(result.exit_code == 0),
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
    )


def _command_for(build_system: BuildSystem, workdir: Path) -> list[str] | None:
    if build_system == BuildSystem.CMAKE:
        return ["cmake", "-B", str(workdir)]
    if build_system == BuildSystem.MESON:
        return ["meson", "setup", str(workdir)]
    if build_system == BuildSystem.AUTOTOOLS:
        return ["./configure"]
    if build_system == BuildSystem.MAKEFILE:
        return ["make", "-n"]
    if build_system == BuildSystem.NINJA:
        return ["ninja", "-n"]
    return None
