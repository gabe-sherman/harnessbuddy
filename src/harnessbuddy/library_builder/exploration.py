from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path

from harnessbuddy.core.subprocesses import run_command_streaming
from harnessbuddy.library_builder.models import (
    AgentReport,
    AnalysisResult,
    BuildExplorationResult,
    BuildPaths,
    BuildSystem,
)
from harnessbuddy.library_builder.scripts import build_library_script


def is_standard_source_layout(analysis: AnalysisResult, workdir: Path) -> bool:
    """True when the source was cloned to workdir/src, the layout generated output
    scaffolds expect.

    When true, build_library.sh's paths can be expressed relative to $SCRIPT_DIR
    (the directory the script lives in), so the same script works unmodified whether
    run from workdir during exploration or copied into a generated output directory.
    """
    return analysis.source_path.resolve() == (workdir / "src").resolve()


def explore(
    analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
) -> BuildExplorationResult:
    """Write a host-native build_library.sh into workdir and run it.

    The script is written to workdir/build_library.sh. When the source uses the
    standard workdir/src layout, its paths are $SCRIPT_DIR-relative so the script
    can be copied verbatim into generated output directories, preserving any agent
    fixes; BuildExplorationResult.script_path is set in that case. Otherwise paths
    fall back to absolute and script_path is left unset. Streams build output to
    stdout in real time. build.env is written to workdir after a successful build.
    """
    workdir = workdir.resolve()
    build_dir = workdir / "build"
    install_dir = workdir / "install"

    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    if install_dir.exists():
        shutil.rmtree(install_dir)
    install_dir.mkdir(parents=True)

    standard_layout = is_standard_source_layout(analysis, workdir)
    source_dir = "$SCRIPT_DIR/src" if standard_layout else str(analysis.source_path.resolve())

    script = build_library_script(
        analysis.build_system,
        BuildPaths(
            source_dir=source_dir,
            build_dir="$SCRIPT_DIR/build",
            install_dir="$SCRIPT_DIR/install",
            env_file="$SCRIPT_DIR/build.env",
        ),
        host_fallbacks=True,
        autotools_setup=analysis.autotools_setup,
    )
    script_path = workdir / "build_library.sh"
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
    result = run_command_streaming(command, workdir, timeout)

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
        script_path=script_path if standard_layout else None,
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return [str(item) for item in value]


def read_agent_report(workdir: Path) -> AgentReport | None:
    """Read and delete workdir/agent_report.json, tolerantly parsing its contents.

    Returns None if the file is absent, isn't valid JSON, or isn't a JSON object.
    Deletes the file whenever it existed, regardless of parse outcome, so a later,
    unrelated invocation never picks up a stale report.
    """
    report_path = workdir / "agent_report.json"
    if not report_path.exists():
        return None
    try:
        data = json.loads(report_path.read_text())
        if not isinstance(data, dict):
            return None
        summary = data.get("summary")
        return AgentReport(
            summary=summary if isinstance(summary, str) else None,
            missing_system_packages=_string_list(data.get("missing_system_packages")),
            extra_include_paths=_string_list(data.get("extra_include_paths")),
            extra_library_paths=_string_list(data.get("extra_library_paths")),
        )
    except (json.JSONDecodeError, OSError):
        return None
    finally:
        report_path.unlink(missing_ok=True)


def _validate_install_artifacts(install_dir: Path) -> list[str]:
    errors = []
    lib_dir = install_dir / "lib"
    if not lib_dir.exists() or not any(lib_dir.glob("*.a")):
        errors.append(f"no static libraries (*.a) found in {lib_dir}")
    include_dir = install_dir / "include"
    if not include_dir.exists() or not any(include_dir.iterdir()):
        errors.append(f"no headers found in {include_dir}")
    return errors
