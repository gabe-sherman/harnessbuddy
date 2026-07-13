from __future__ import annotations

import dataclasses
import logging
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from harnessbuddy.core.subprocesses import Runner, RunResult, run_command, run_command_streaming
from harnessbuddy.library_builder.environments import verification
from harnessbuddy.library_builder.environments.base import Environment, EnvironmentUnavailableError
from harnessbuddy.library_builder.oss_fuzz import workspace

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
        Language,
    )

_AVAILABILITY_TIMEOUT_SECONDS = 10
_IMAGE_BUILD_TIMEOUT_SECONDS = 600

# Docker pull/network-failure phrases (research.md #3) — distinguishes "the environment
# itself isn't reachable" (no agent fallback, FR-007) from a genuine build failure.
_UNAVAILABLE_PATTERN = re.compile(
    "|".join((r"Error response from daemon", r"no such host", r"i/o timeout")),
    re.IGNORECASE,
)


class _ImageBuildError(Exception):
    """The run-scoped image failed to build for a reason other than Docker/network
    unavailability."""

    def __init__(self, command: list[str], result: RunResult) -> None:
        super().__init__(result.stderr or result.stdout)
        self.command = command
        self.result = result


def _is_environment_unavailable(combined_output: str) -> bool:
    return bool(_UNAVAILABLE_PATTERN.search(combined_output))


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _docker_run_factory(image_tag: str, language: Language, extra_mounts: list[Path]) -> Runner:
    """Build a Runner that executes command inside image_tag via `docker run --entrypoint bash`.

    cwd is always bind-mounted at the same absolute path (matching the $SCRIPT_DIR-relative
    convention exploration.py/harness_explorer.py already use), so scripts referencing
    absolute host paths keep working unmodified inside the container. extra_mounts covers
    the non-standard-layout case, where the analyzed source lives outside the workdir tree.
    Used only for internal probing (the exploration-time build, harness-link discovery
    attempts) — the atomic pass/fail gate always goes through verification.run_docker_verification
    instead (FR-002).
    """

    def run(command: list[str], cwd: Path, timeout: int) -> RunResult:
        mount_paths = dict.fromkeys([str(cwd.resolve()), *(str(p.resolve()) for p in extra_mounts)])
        docker_command = ["docker", "run", "--rm", "--entrypoint", "bash"]
        docker_command += ["-e", f"FUZZING_LANGUAGE={language.value}"]
        for mount in mount_paths:
            docker_command += ["-v", f"{mount}:{mount}"]
        docker_command += ["-w", str(cwd.resolve()), image_tag, "-c", shlex.join(command)]
        logger.debug("Running docker command from %s:\n%s", str(cwd), " ".join(docker_command))
        return run_command_streaming(docker_command, cwd, timeout)

    return run


class OssFuzzExecutor:
    """Runs each pipeline stage against the real OSS-Fuzz project layout, materialized
    directly in the workspace as soon as its pieces are known (User Story 2), and gates
    pass/fail via the same check_docker_build.sh script the repair agent uses (FR-001,
    FR-002).

    Stateful per instance: run_library_build builds (or reuses) a run-scoped image tagged
    from the project name and the current apt-package set, used for internal probing
    (the bind-mounted exploration build, harness-link discovery attempts); run_harness_compile
    reuses that same image for its discovery loop. The actual pass/fail gate for each stage
    is a fresh, from-scratch `docker build` + `compile` via check_docker_build.sh, independent
    of this run-scoped image.
    """

    def __init__(self) -> None:
        self._image_tag: str | None = None
        self._built_apt_packages: tuple[str, ...] | None = None
        self._project_name: str | None = None

    def check_availability(self) -> None:
        result = run_command(["docker", "info"], Path.cwd(), _AVAILABILITY_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            detail = (
                result.stderr.strip() or result.stdout.strip() or f"exit code {result.exit_code}"
            )
            raise EnvironmentUnavailableError(
                f"Docker daemon not reachable: {detail}", Environment.OSS_FUZZ
            )

    def run_library_build(
        self, analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
    ) -> BuildExplorationResult:
        from harnessbuddy.library_builder.exploration import explore
        from harnessbuddy.library_builder.models import BuildExplorationResult

        workdir = workdir.resolve()
        self._project_name = analysis.project_name
        self._materialize_workspace(workdir, analysis)

        try:
            self._ensure_image(workdir, analysis)
        except _ImageBuildError as exc:
            return BuildExplorationResult(
                build_system=analysis.build_system,
                succeeded=False,
                command=exc.command,
                stdout=exc.result.stdout,
                stderr=exc.result.stderr,
                exit_code=exc.result.exit_code,
                duration_seconds=exc.result.duration_seconds,
                environment=Environment.OSS_FUZZ,
            )
        if self._image_tag is None:
            raise RuntimeError("_ensure_image did not set an image tag")

        extra_mounts = (
            [] if _is_within(analysis.source_path, workdir) else [analysis.source_path.resolve()]
        )
        run = _docker_run_factory(self._image_tag, analysis.language, extra_mounts)
        exploration_result = explore(
            analysis, workdir, timeout=timeout, environment=Environment.OSS_FUZZ, run=run
        )
        if not exploration_result.command:
            # No real build attempt was made (e.g. unknown build system) — nothing for
            # the shared verification script to check.
            return exploration_result

        verified = verification.run_docker_verification(workdir, analysis.project_name)
        if _is_environment_unavailable(verified.stdout + verified.stderr):
            raise EnvironmentUnavailableError(
                f"Docker became unavailable during verification: {verified.stdout.strip()}",
                Environment.OSS_FUZZ,
            )
        return dataclasses.replace(
            exploration_result,
            succeeded=verified.passed,
            command=verified.command,
            stdout=verified.stdout,
            stderr=verified.stderr,
            exit_code=0 if verified.passed else 1,
            duration_seconds=verified.duration_seconds,
        )

    def run_harness_compile(
        self,
        install_dir: Path,
        workdir: Path,
        language: Language,
        *,
        extra_include_paths: list[str] | None = None,
        extra_library_paths: list[str] | None = None,
    ) -> HarnessExplorationResult:
        from harnessbuddy.library_builder.harness_explorer import explore_harness_compilation

        if self._image_tag is None or self._project_name is None:
            raise RuntimeError(
                "OssFuzzExecutor.run_harness_compile requires a prior successful "
                "run_library_build on this instance to establish the run-scoped image"
            )
        workdir = workdir.resolve()
        # Discovery keeps its fast, direct-exec path against the already-built image for
        # its internal retry loop (research.md #2) — no docker build/compile per attempt.
        run = _docker_run_factory(self._image_tag, language, [])
        harness_result = explore_harness_compilation(
            install_dir,
            workdir,
            language,
            extra_include_paths=extra_include_paths,
            extra_library_paths=extra_library_paths,
            environment=Environment.OSS_FUZZ,
            run=run,
        )
        if not harness_result.static_libs:
            # No install artifacts to link against — nothing for the shared
            # verification script to check.
            return harness_result

        verified = verification.run_docker_verification(workdir, self._project_name)
        if _is_environment_unavailable(verified.stdout + verified.stderr):
            raise EnvironmentUnavailableError(
                f"Docker became unavailable during verification: {verified.stdout.strip()}",
                Environment.OSS_FUZZ,
            )
        return dataclasses.replace(
            harness_result,
            succeeded=verified.passed,
            command=verified.command,
            stdout=verified.stdout,
            stderr=verified.stderr,
            exit_code=0 if verified.passed else 1,
            duration_seconds=verified.duration_seconds,
        )

    def _materialize_workspace(self, workdir: Path, analysis: AnalysisResult) -> None:
        """Write the real OSS-Fuzz project layout directly into the workspace, as soon
        as its pieces are known (User Story 2), instead of a separate synthetic
        representation only assembled at final generation.
        """
        workspace.write_project_yaml(workdir, analysis)
        workspace.write_dockerfile(workdir, analysis, include_bear=True)
        workspace.write_build_sh(workdir)
        harness_source_dir = workdir / "harness_source"
        harness_source_dir.mkdir(exist_ok=True)

        compile_harnesses_path = workdir / "compile_harnesses.sh"
        if not compile_harnesses_path.exists():
            from harnessbuddy.library_builder.oss_fuzz.generation import (
                _COMPILE_HARNESSES_SH_STUB,
            )
            from harnessbuddy.library_builder.scripts import write_default_fuzzer

            # The stub compiles whatever's in harness_source/ (research.md #3) — write
            # the real default fuzzer stub now so check_docker_build.sh's /out non-empty
            # check has something to find even before harness-link discovery ever runs.
            write_default_fuzzer(harness_source_dir, analysis)
            compile_harnesses_path.write_text(_COMPILE_HARNESSES_SH_STUB)
            compile_harnesses_path.chmod(compile_harnesses_path.stat().st_mode | 0o111)

    def _ensure_image(self, workdir: Path, analysis: AnalysisResult) -> None:
        """Build (or reuse) a run-scoped image from the workspace's real Dockerfile —
        used for internal probing (the bind-mounted exploration build, harness-link
        discovery attempts), not the atomic pass/fail gate (research.md #1, #2).
        """
        packages = tuple(analysis.system_packages)
        tag = f"harnessbuddy-dev/{analysis.project_name}:latest"
        if self._image_tag == tag and self._built_apt_packages == packages:
            return

        command = ["docker", "build", "-t", tag, "."]
        result = run_command(command, workdir, _IMAGE_BUILD_TIMEOUT_SECONDS)

        if result.exit_code != 0:
            if _is_environment_unavailable(result.stdout + result.stderr):
                raise EnvironmentUnavailableError(
                    f"Failed to build the oss-fuzz image: {result.stderr.strip()}",
                    Environment.OSS_FUZZ,
                )
            raise _ImageBuildError(command, result)

        self._image_tag = tag
        self._built_apt_packages = packages
