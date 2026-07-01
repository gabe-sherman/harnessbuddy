from __future__ import annotations

import shutil
import stat
from pathlib import Path

from harnessbuddy.core.subprocesses import run_command_streaming
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    BuildPaths,
    BuildSystem,
)
from harnessbuddy.library_builder.scripts import build_library_script


def explore(
    analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
) -> BuildExplorationResult:
    """Write a host-native build_library.sh into the source tree and run it.

    The script is written to analysis.source_path/build_library.sh with absolute
    paths for build_dir and install_dir under workdir. Streams build output to
    stdout in real time. build.env is written to workdir after a successful build.
    """
    workdir = workdir.resolve()
    build_dir = workdir / "build"
    install_dir = workdir / "install"
    env_file = workdir / "build.env"

    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    if install_dir.exists():
        shutil.rmtree(install_dir)
    install_dir.mkdir(parents=True)

    script = build_library_script(
        analysis.build_system,
        BuildPaths(
            source_dir=str(analysis.source_path.resolve()),
            build_dir=str(build_dir),
            install_dir=str(install_dir),
            env_file=str(env_file),
        ),
        host_fallbacks=True,
        autotools_setup=analysis.autotools_setup,
    )
    script_path = analysis.source_path / "build_library.sh"
    script_path.write_text(script)
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if analysis.build_system == BuildSystem.UNKNOWN:
        return BuildExplorationResult(
            build_system=analysis.build_system,
            succeeded=False,
            command=[],
            stdout="",
            stderr="",
            exit_code=-1,
            duration_seconds=0.0,
        )

    command = ["bash", str(script_path.name)]
    result = run_command_streaming(command, analysis.source_path, timeout)

    succeeded = result.exit_code == 0
    stderr = result.stderr
    if succeeded:
        validation_errors = _validate_install_artifacts(install_dir)
        if validation_errors:
            succeeded = False
            stderr += "\n" + "\n".join(validation_errors)

    return BuildExplorationResult(
        build_system=analysis.build_system,
        succeeded=succeeded,
        command=command,
        stdout=result.stdout,
        stderr=stderr,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
    )


def _validate_install_artifacts(install_dir: Path) -> list[str]:
    errors = []
    lib_dir = install_dir / "lib"
    if not lib_dir.exists() or not any(lib_dir.glob("*.a")):
        errors.append(f"no static libraries (*.a) found in {lib_dir}")
    include_dir = install_dir / "include"
    if not include_dir.exists() or not any(include_dir.iterdir()):
        errors.append(f"no headers found in {include_dir}")
    return errors
