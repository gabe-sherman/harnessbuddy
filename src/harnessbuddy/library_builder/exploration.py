from __future__ import annotations

import json
import logging
import shutil
import stat
import tempfile
from pathlib import Path

from harnessbuddy.core.subprocesses import Runner, run_command_streaming
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import (
    AgentReport,
    AnalysisResult,
    BuildExplorationResult,
    BuildPaths,
    BuildSystem,
)
from harnessbuddy.library_builder.scripts import build_library_script

logger = logging.getLogger(__name__)

_BEAR_NOT_FOUND_ERROR = (
    "bear not found on PATH; install bear to capture compile_commands.json for "
    "Make/Autotools builds"
)

_MAKE_LIKE_SYSTEMS = (BuildSystem.MAKEFILE, BuildSystem.AUTOTOOLS)


def is_standard_source_layout(analysis: AnalysisResult, workdir: Path) -> bool:
    """True when the source was cloned to workdir/src, the layout generated output
    scaffolds expect.

    When true, build_library.sh's paths can be expressed relative to $SCRIPT_DIR
    (the directory the script lives in), so the same script works unmodified whether
    run from workdir during exploration or copied into a generated output directory.
    """
    return analysis.source_path.resolve() == (workdir / "src").resolve()


def write_build_library_script(
    analysis: AnalysisResult, workdir: Path, *, environment: Environment = Environment.LOCAL
) -> tuple[Path, bool]:
    """Write build_library.sh into workdir/build_library.sh.

    When the source uses the standard workdir/src layout, its paths are
    $SCRIPT_DIR-relative so the script can be copied verbatim into generated output
    directories, preserving any agent fixes. Otherwise paths fall back to absolute.
    Returns (script_path, standard_layout) so callers can decide whether
    BuildExplorationResult.script_path should be set.

    Split out of explore() so OssFuzzExecutor can write this file before building the
    workspace image — the Dockerfile's COPY of build_library.sh requires it to already
    exist on disk.
    """
    workdir = workdir.resolve()
    standard_layout = is_standard_source_layout(analysis, workdir)
    source_dir = "$SCRIPT_DIR/src" if standard_layout else str(analysis.source_path.resolve())

    script = build_library_script(
        analysis.build_system,
        BuildPaths(
            source_dir=source_dir,
            build_dir="$BUILD_PREFIX/build",
            install_dir="$BUILD_PREFIX/install",
        ),
        host_fallbacks=environment is Environment.LOCAL,
        autotools_setup=analysis.autotools_setup,
    )
    script_path = workdir / "build_library.sh"
    script_path.write_text(script)
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path, standard_layout


def explore(  # noqa: PLR0913 -- 4 of 6 are keyword-only with defaults, each independently meaningful
    analysis: AnalysisResult,
    workdir: Path,
    *,
    timeout: int = 300,
    environment: Environment = Environment.LOCAL,
    run: Runner | None = None,
    regenerate_script: bool = True,
) -> BuildExplorationResult:
    """Write a build_library.sh into workdir and run it in the given environment.

    environment selects host CC/CXX/CFLAGS/CXXFLAGS fallbacks (Environment.LOCAL only —
    Environment.OSS_FUZZ relies on the container image's own toolchain env) and is
    recorded on the returned result. run defaults to streaming the command as a host
    subprocess; callers running this inside a container (e.g. OssFuzzExecutor) pass a
    run primitive that wraps the command in a `docker run` invocation instead.

    regenerate_script=False reuses the existing workdir/build_library.sh verbatim instead
    of rewriting it from the template — used to re-run a repair agent's already-fixed
    script (which write_build_library_script would otherwise clobber) purely to capture
    host-side install/ artifacts and compile_commands.json that the agent's own
    out-of-band verification (e.g. oss-fuzz's unmounted check_docker_build.sh) doesn't
    produce.
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

    if regenerate_script:
        script_path, standard_layout = write_build_library_script(
            analysis, workdir, environment=environment
        )
    else:
        script_path = workdir / "build_library.sh"
        standard_layout = is_standard_source_layout(analysis, workdir)

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
        validation_errors = _validate_install_artifacts(install_dir)
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
    environment (bear is guaranteed present there, FR-011); on the local host it's
    best-effort via shutil.which, since a missing bear must never turn into a build
    failure (FR-008) — bear_missing_error is set instead so the caller can report it
    once the build itself has succeeded.
    """
    plain = ["bash", str(script_path.name)]
    if build_system not in _MAKE_LIKE_SYSTEMS:
        return plain, None
    if environment is Environment.OSS_FUZZ or shutil.which("bear") is not None:
        return ["bear", "--", *plain], None
    return plain, _BEAR_NOT_FOUND_ERROR


def _capture_compile_commands(  # noqa: PLR0913 -- standard_layout/build_dir are keyword-only, independently meaningful
    analysis: AnalysisResult,
    workdir: Path,
    runner: Runner,
    timeout: int,
    bear_missing_error: str | None,
    *,
    standard_layout: bool,
    build_dir: Path | None = None,
) -> tuple[Path | None, str | None]:
    """Capture compile_commands.json as a byproduct of the build that just succeeded.

    Returns (path, error) — exactly one is non-None. CMake re-configures with
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON (no rebuild needed, since CMake writes the file
    during configure); Meson's Ninja backend already wrote the file unprompted; Make/
    Autotools relies on the `bear --` wrap explore() already applied (or didn't, if
    bear_missing_error is set) to the canonical build command above.

    The CMake configure command below runs with cwd=workdir (runner(..., workdir, ...)),
    so it must reference the source by a path relative to that cwd — not an absolute host
    path — since Environment.OSS_FUZZ's runner bind-mounts workdir at /src inside the
    container (not at its own host path), where an absolute host path wouldn't resolve to
    anything. standard_layout=True means the source lives at workdir/src, referenceable
    the same "src"-relative way; the non-standard-layout case keeps the absolute host path
    since that's mounted separately, at its own path, regardless of workdir's mount target.

    build_dir defaults to workdir/build (the build the caller just ran) — overridden by
    recapture_compile_commands_after_agent_fix to point at a scratch directory instead,
    so a supplemental capture command never touches the real workdir/build or install/.
    That override only ever runs on the host directly (Environment.LOCAL), so it's safe
    to pass as an absolute path — unlike the default case's "-B build", which must stay
    cwd-relative for the same bind-mount reason -S does (see above).
    """
    build_dir_override = build_dir
    build_dir = build_dir_override if build_dir_override is not None else workdir / "build"
    build_arg = str(build_dir_override) if build_dir_override is not None else "build"
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


def recapture_compile_commands_after_agent_fix(
    analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
) -> tuple[Path | None, str | None]:
    """Capture compile_commands.json for a build_library.sh that already succeeded via
    an out-of-band verification (an agent's own check_local_build.sh run), without
    touching the already-verified workdir/install or workdir/build.

    Runs the same, unmodified script again with BUILD_PREFIX overridden to a scratch
    directory: build_library.sh's skip-if-already-built guard (scripts.py) only
    inspects BUILD_PREFIX's install dir, so this reproduces a full build in complete
    isolation from workdir/install rather than short-circuiting against it. A failure
    here can never regress the already-verified install/, since it never touches it —
    the scratch directory (and everything built into it) is discarded once compile
    commands are extracted from it.
    """
    if analysis.build_system == BuildSystem.UNKNOWN:
        return None, None

    workdir = workdir.resolve()
    script_path = workdir / "build_library.sh"
    if not script_path.exists():
        return None, "build_library.sh not found"

    standard_layout = is_standard_source_layout(analysis, workdir)
    command, bear_missing_error = _build_command(
        analysis.build_system, Environment.LOCAL, script_path
    )
    if bear_missing_error is not None and analysis.build_system in _MAKE_LIKE_SYSTEMS:
        # Nothing to gain from paying for a rebuild bear can't capture.
        return None, bear_missing_error

    with tempfile.TemporaryDirectory(prefix="harnessbuddy-recapture-") as scratch:
        scratch_dir = Path(scratch)
        env_command = ["env", f"BUILD_PREFIX={scratch_dir}", *command]
        result = run_command_streaming(env_command, workdir, timeout)
        if result.exit_code != 0:
            return None, (
                "recapture build (scratch BUILD_PREFIX, to capture compile_commands.json "
                "without touching the already-verified install/) failed"
            )

        return _capture_compile_commands(
            analysis,
            workdir,
            run_command_streaming,
            timeout,
            bear_missing_error,
            standard_layout=standard_layout,
            build_dir=scratch_dir / "build",
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
            missing_libs=_string_list(data.get("missing_libs")),
            missing_apt_packages=_string_list(data.get("missing_apt_packages")),
            missing_brew_packages=_string_list(data.get("missing_brew_packages")),
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
