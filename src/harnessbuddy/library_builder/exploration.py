from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from harnessbuddy.core.files import write_executable
from harnessbuddy.core.subprocesses import Runner, run_command_streaming
from harnessbuddy.library_builder.build_parameters import BuildParameters
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import (
    AgentReport,
    AnalysisResult,
    BuildExplorationResult,
    BuildPaths,
    BuildSystem,
)
from harnessbuddy.library_builder.scripts import build_library_script
from harnessbuddy.library_builder.timeouts import DEFAULT_BUILD_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_BEAR_NOT_FOUND_ERROR = (
    "bear not found on PATH; install bear to capture compile_commands.json for "
    "Make/Autotools builds"
)

_MAKE_LIKE_SYSTEMS = (BuildSystem.MAKEFILE, BuildSystem.AUTOTOOLS)


def is_standard_source_layout(analysis: AnalysisResult, workdir: Path) -> bool:
    """True when the source was cloned to workdir/src, the layout generated output expects.

    build_library.sh's paths can then be $SCRIPT_DIR-relative, so the same script works
    unmodified from the workspace and from a generated output directory.
    """
    return analysis.source_path.resolve() == (workdir / "src").resolve()


def write_build_library_script(
    analysis: AnalysisResult, workdir: Path, *, parameters: BuildParameters | None = None
) -> tuple[Path, bool]:
    """Write build_library.sh into workdir/build_library.sh.

    With the standard workdir/src layout the paths are $SCRIPT_DIR-relative, so the script
    can be published verbatim; otherwise they fall back to absolute. Returns (script_path,
    standard_layout) so callers know whether BuildExplorationResult.script_path can be set.

    Separate from explore() because OssFuzzExecutor needs this file on disk before building
    the workspace image, which COPYs it.
    """
    workdir = workdir.resolve()
    standard_layout = is_standard_source_layout(analysis, workdir)
    source_dir = "$SCRIPT_DIR/src" if standard_layout else str(analysis.source_path.resolve())

    parameters = parameters or BuildParameters.defaults()
    script = build_library_script(
        analysis.build_system,
        BuildPaths(
            source_dir=source_dir,
            build_dir="$BUILD_PREFIX/build",
            install_dir="$BUILD_PREFIX/install",
        ),
        autotools_setup=analysis.autotools_setup,
        configure_args=parameters.library_configure_args,
        cc=parameters.cc,
        cxx=parameters.cxx,
        cflags=parameters.library_cflags,
        cxxflags=parameters.library_cxxflags,
    )
    script_path = write_executable(workdir / "build_library.sh", script)
    return script_path, standard_layout


def explore(  # noqa: PLR0913 -- 4 keyword-only inputs, each independently meaningful
    analysis: AnalysisResult,
    workdir: Path,
    *,
    timeout: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
    environment: Environment = Environment.LOCAL,
    run: Runner | None = None,
    parameters: BuildParameters | None = None,
) -> BuildExplorationResult:
    """Write a build_library.sh into workdir and run it in the given environment.

    environment decides whether the build command is wrapped with `bear`, and is recorded on
    the returned result; the script text itself is environment-independent. run defaults to
    streaming as a host subprocess; a caller building inside a container passes a run
    primitive that wraps the command in `docker run`.
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

    script_path, standard_layout = write_build_library_script(
        analysis, workdir, parameters=parameters
    )

    if analysis.build_system == BuildSystem.UNKNOWN:
        return BuildExplorationResult(
            build_system=analysis.build_system,
            succeeded=False,
            command=[],
            stdout="",
            stderr="",
            exit_code=-1,
            duration_seconds=0.0,
            environment=environment,
        )

    command, bear_missing_error = _build_command(analysis.build_system, environment, script_path)

    runner = run if run is not None else run_command_streaming
    result = runner(command, workdir, timeout)

    succeeded = result.exit_code == 0
    stderr = result.stderr
    if succeeded:
        validation_errors = validate_install_artifacts(install_dir)
        if validation_errors:
            succeeded = False
            stderr += "\n" + "\n".join(validation_errors)

    compile_commands_path: Path | None = None
    compile_commands_error: str | None = None
    if succeeded:
        compile_commands_path, compile_commands_error = _capture_compile_commands(
            analysis, workdir, runner, timeout, bear_missing_error, standard_layout=standard_layout
        )

    return BuildExplorationResult(
        build_system=analysis.build_system,
        succeeded=succeeded,
        command=command,
        stdout=result.stdout,
        stderr=stderr,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        install_dir=install_dir,
        script_path=script_path if standard_layout else None,
        environment=environment,
        compile_commands_path=compile_commands_path,
        compile_commands_error=compile_commands_error,
    )


def _build_command(
    build_system: BuildSystem, environment: Environment, script_path: Path
) -> tuple[list[str], str | None]:
    """Choose the canonical build invocation, wrapping Make/Autotools with `bear --`.

    Returns (command, bear_missing_error). The wrap is unconditional in the oss-fuzz
    environment, where bear is always present. On the local host it is best-effort: a missing
    bear must not fail the build, so bear_missing_error is set for the caller to report once
    the build itself has succeeded.
    """
    plain = ["bash", str(script_path.name)]
    if build_system not in _MAKE_LIKE_SYSTEMS:
        return plain, None
    if environment is Environment.OSS_FUZZ or shutil.which("bear") is not None:
        return ["bear", "--", *plain], None
    return plain, _BEAR_NOT_FOUND_ERROR


def _capture_compile_commands(  # noqa: PLR0913 -- standard_layout is keyword-only and independently meaningful
    analysis: AnalysisResult,
    workdir: Path,
    runner: Runner,
    timeout: int,
    bear_missing_error: str | None,
    *,
    standard_layout: bool,
) -> tuple[Path | None, str | None]:
    """Capture compile_commands.json as a byproduct of the build that just succeeded.

    Returns (path, error) — exactly one is non-None. CMake re-configures with
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON, which needs no rebuild since CMake writes the file
    during configure. Meson's Ninja backend already wrote it. Make/Autotools relies on the
    `bear --` wrap explore() applied, or on bear_missing_error if it could not.

    The CMake configure below runs with cwd=workdir, and the oss-fuzz runner bind-mounts
    workdir at /src, so a standard-layout source must be referenced as cwd-relative "src".
    A non-standard-layout source keeps its absolute host path, which is mounted separately.
    """
    build_dir = workdir / "build"
    build_arg = "build"
    target = workdir / "compile_commands.json"

    if analysis.build_system == BuildSystem.CMAKE:
        source_arg = "src" if standard_layout else str(analysis.source_path.resolve())
        configure_command = [
            "cmake",
            "-B",
            build_arg,
            "-S",
            source_arg,
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ]
        configure_result = runner(configure_command, workdir, timeout)
        source = build_dir / "compile_commands.json"
        if configure_result.exit_code != 0 or not source.exists():
            return None, "cmake re-configure did not produce compile_commands.json"
        shutil.copy2(source, target)
        return target, None

    if analysis.build_system == BuildSystem.MESON:
        source = build_dir / "compile_commands.json"
        if not source.exists():
            return None, "meson build did not produce compile_commands.json"
        shutil.copy2(source, target)
        return target, None

    if analysis.build_system in _MAKE_LIKE_SYSTEMS:
        if bear_missing_error is not None:
            return None, bear_missing_error
        if not target.exists():
            return None, "bear did not produce compile_commands.json"
        return target, None

    return None, None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return [str(item) for item in value]


def read_agent_report(workdir: Path) -> AgentReport | None:
    """Read and delete workdir/agent_report.json, tolerantly parsing its contents.

    Returns None if the file is absent, is not valid JSON, or is not a JSON object. Deletes
    the file whenever it existed, so a later invocation cannot pick up a stale report.
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
            missing_libs=_string_list(data.get("missing_libs")),
            missing_apt_packages=_string_list(data.get("missing_apt_packages")),
            extra_include_paths=_string_list(data.get("extra_include_paths")),
            extra_library_paths=_string_list(data.get("extra_library_paths")),
        )
    except (json.JSONDecodeError, OSError):
        return None
    finally:
        report_path.unlink(missing_ok=True)


def validate_install_artifacts(install_dir: Path) -> list[str]:
    errors = []
    lib_dir = install_dir / "lib"
    if not lib_dir.exists() or not any(lib_dir.glob("*.a")):
        errors.append(f"no static libraries (*.a) found in {lib_dir}")
    include_dir = install_dir / "include"
    if not include_dir.exists() or not any(include_dir.iterdir()):
        errors.append(f"no headers found in {include_dir}")
    return errors
