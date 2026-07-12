from __future__ import annotations

import re
import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from harnessbuddy.core.subprocesses import Runner, RunResult, run_command, run_command_streaming
from harnessbuddy.library_builder.environments.base import Environment, EnvironmentUnavailableError

if TYPE_CHECKING:
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
        Language,
    )

_AVAILABILITY_TIMEOUT_SECONDS = 10
_PROBE_IMAGE_BUILD_TIMEOUT_SECONDS = 600
_BASE_IMAGE = "gcr.io/oss-fuzz-base/base-builder"

# Docker pull/network-failure phrases (research.md #3) — distinguishes "the environment
# itself isn't reachable" (no agent fallback, FR-012) from a genuine build failure.
_UNAVAILABLE_PATTERN = re.compile(
    "|".join((r"Error response from daemon", r"no such host", r"i/o timeout")),
    re.IGNORECASE,
)


class _ProbeImageBuildError(Exception):
    """The probe image failed to build for a reason other than Docker/network unavailability."""

    def __init__(self, command: list[str], result: RunResult) -> None:
        super().__init__(result.stderr or result.stdout)
        self.command = command
        self.result = result


def _is_environment_unavailable(stderr: str) -> bool:
    return bool(_UNAVAILABLE_PATTERN.search(stderr))


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _docker_run_factory(image_tag: str, extra_mounts: list[Path]) -> Runner:
    """Build a Runner that executes command inside image_tag via `docker run --entrypoint bash`.

    cwd is always bind-mounted at the same absolute path (matching the $SCRIPT_DIR-relative
    convention exploration.py/harness_explorer.py already use), so scripts referencing
    absolute host paths keep working unmodified inside the container. extra_mounts covers
    the non-standard-layout case, where the analyzed source lives outside the workdir tree.
    """

    def run(command: list[str], cwd: Path, timeout: int) -> RunResult:
        mount_paths = dict.fromkeys([str(cwd.resolve()), *(str(p.resolve()) for p in extra_mounts)])
        docker_command = ["docker", "run", "--rm", "--entrypoint", "bash"]
        for mount in mount_paths:
            docker_command += ["-v", f"{mount}:{mount}"]
        docker_command += ["-w", str(cwd.resolve()), image_tag, "-c", shlex.join(command)]
        return run_command_streaming(docker_command, cwd, timeout)

    return run


class OssFuzzExecutor:
    """Runs each pipeline stage inside the real OSS-Fuzz base-builder container.

    Stateful per instance: run_library_build builds (or reuses) a run-scoped probe image
    tagged from the project name and the current apt-package set, then run_harness_compile
    reuses that same image — both stages share state via workdir's bind mount rather than a
    long-lived container (research.md #1).
    """

    def __init__(self) -> None:
        self._image_tag: str | None = None
        self._built_apt_packages: tuple[str, ...] | None = None

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
        try:
            self._ensure_probe_image(analysis)
        except _ProbeImageBuildError as exc:
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
            raise RuntimeError("_ensure_probe_image did not set an image tag")
        extra_mounts = (
            [] if _is_within(analysis.source_path, workdir) else [analysis.source_path.resolve()]
        )
        run = _docker_run_factory(self._image_tag, extra_mounts)
        return explore(
            analysis, workdir, timeout=timeout, environment=Environment.OSS_FUZZ, run=run
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

        if self._image_tag is None:
            raise RuntimeError(
                "OssFuzzExecutor.run_harness_compile requires a prior successful "
                "run_library_build on this instance to establish the probe image"
            )
        run = _docker_run_factory(self._image_tag, [])
        return explore_harness_compilation(
            install_dir,
            workdir,
            language,
            extra_include_paths=extra_include_paths,
            extra_library_paths=extra_library_paths,
            environment=Environment.OSS_FUZZ,
            run=run,
        )

    def _ensure_probe_image(self, analysis: AnalysisResult) -> None:
        packages = tuple(analysis.system_packages)
        tag = f"harnessbuddy-probe/{analysis.project_name}:latest"
        if self._image_tag == tag and self._built_apt_packages == packages:
            return

        # bear is a hard requirement in this container (FR-011): unlike the local
        # host's best-effort shutil.which check, HarnessBuddy provisions this image
        # itself, so Make/Autotools compile-commands capture must never depend on
        # whatever the repository's own analysis.system_packages happens to include.
        all_packages = " ".join(("bear", *packages))
        dockerfile = (
            f"FROM {_BASE_IMAGE}\n"
            f"RUN apt-get update && apt-get install -y --no-install-recommends {all_packages}\n"
        )

        with tempfile.TemporaryDirectory(prefix="harnessbuddy-probe-") as raw_context_dir:
            context_dir = Path(raw_context_dir)
            (context_dir / "Dockerfile").write_text(dockerfile)
            command = ["docker", "build", "-t", tag, "."]
            result = run_command(command, context_dir, _PROBE_IMAGE_BUILD_TIMEOUT_SECONDS)

        if result.exit_code != 0:
            if _is_environment_unavailable(result.stderr):
                raise EnvironmentUnavailableError(
                    f"Failed to build the oss-fuzz probe image: {result.stderr.strip()}",
                    Environment.OSS_FUZZ,
                )
            raise _ProbeImageBuildError(command, result)

        self._image_tag = tag
        self._built_apt_packages = packages
