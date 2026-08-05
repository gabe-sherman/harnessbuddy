from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from harnessbuddy.core.subprocesses import Runner, RunResult, run_command, run_command_streaming
from harnessbuddy.library_builder.environments import verification
from harnessbuddy.library_builder.environments.base import Environment, EnvironmentUnavailableError
from harnessbuddy.library_builder.timeouts import DEFAULT_BUILD_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from harnessbuddy.library_builder.build_parameters import BuildParameters
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
        Language,
    )

_AVAILABILITY_TIMEOUT_SECONDS = 10
_IMAGE_BUILD_TIMEOUT_SECONDS = 600

# Docker pull/network-failure phrases. An unreachable environment is not a build failure,
# so it never routes to agent fallback.
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


_CONTAINER_SRC_DIR = "/src"


def _ensure_source_symlink(workdir: Path, analysis: AnalysisResult) -> None:
    """Symlink workdir/src to the real source when it lives elsewhere.

    is_standard_source_layout then accepts the layout, so build_library.sh gets the portable
    $SCRIPT_DIR/src path instead of a host-only absolute path no container can resolve.
    """
    link = workdir / "src"
    source = analysis.source_path.resolve()
    if link.resolve() == source:
        return
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(source)


def _docker_run_factory(
    image_tag: str, extra_mounts: list[Path], environment_variables: dict[str, str] | None = None
) -> Runner:
    """Build a Runner that executes command inside image_tag via `docker run --entrypoint bash`.

    cwd is bind-mounted at /src, not at its own host path, because OSS-Fuzz tooling (the
    base image's `compile`, $LIB_FUZZING_ENGINE setup) is hardwired to /src. Mounting
    elsewhere would leave that tooling operating on the stale snapshot baked into the image
    instead of the live workspace harness-link discovery rewrites between attempts.

    extra_mounts covers a source tree outside workdir, which keeps mounting at its own host
    path. Used for internal probing only; the pass/fail gate goes through verification.py.
    """

    def run(command: list[str], cwd: Path, timeout: int) -> RunResult:
        mounts = ["-v", f"{cwd.resolve()}:{_CONTAINER_SRC_DIR}"]
        for mount in dict.fromkeys(str(p.resolve()) for p in extra_mounts):
            mounts += ["-v", f"{mount}:{mount}"]
        docker_command = ["docker", "run", "--rm", "--entrypoint", "bash"]
        for name, value in (environment_variables or {}).items():
            docker_command += ["-e", f"{name}={value}"]
        docker_command += mounts
        docker_command += ["-w", _CONTAINER_SRC_DIR, image_tag, "-c", shlex.join(command)]
        logger.debug("Running docker command from %s:\n%s", str(cwd), shlex.join(docker_command))
        result = run_command_streaming(docker_command, cwd, timeout)
        _restore_host_ownership(image_tag, mounts, cwd)
        return result

    return run


_CHOWN_TIMEOUT_SECONDS = 60


def _restore_host_ownership(image_tag: str, mounts: list[str], cwd: Path) -> None:
    """Give the host user back what the container just wrote into the bind mount.

    The OSS-Fuzz image runs as root, so build/, install/, and out/ come back owned by uid 0
    and the next attempt's rmtree of install/ fails. -h chowns <workspace>/src itself rather
    than following it, since a non-standard-layout run leaves it a symlink into the user's
    own tree. Best-effort: on Docker Desktop there is nothing to undo.
    """
    command = [
        *["docker", "run", "--rm", *mounts, "--entrypoint", "chown", image_tag],
        *["-Rh", f"{os.getuid()}:{os.getgid()}", _CONTAINER_SRC_DIR],
    ]
    result = run_command(command, cwd, _CHOWN_TIMEOUT_SECONDS)
    if result.exit_code != 0:
        logger.debug("Could not restore host ownership of %s: %s", cwd, result.output.strip())


# What the base image already sets correctly for a fuzzing build. Only values that differ
# travel into the container: forwarding an empty CFLAGS would wipe the image's sanitizer
# flags.
_UNCHOSEN_COMPILER_SETTINGS = {"CC": "clang", "CXX": "clang++", "CFLAGS": "", "CXXFLAGS": ""}
_UNCHOSEN_HARNESS_FLAGS = "-fsanitize=fuzzer"


def _chosen_settings(values: dict[str, str], *, unchosen: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in values.items() if value != unchosen.get(name, "")}


def _container_build_environment(parameters: BuildParameters) -> dict[str, str]:
    """The library-build settings to forward into the container.

    CMAKE_EXPORT_COMPILE_COMMANDS is named here rather than left to the host process, because
    `docker run` forwards nothing: compile_commands_capture_environment() sets it on the host
    and CMake inside the container never sees it. Unconditional, unlike the compiler settings
    -- it is capture configuration, not a build choice a user can make.
    """
    return {
        "CMAKE_EXPORT_COMPILE_COMMANDS": "ON",
        **_chosen_settings(
            {
                "CC": parameters.cc,
                "CXX": parameters.cxx,
                "CFLAGS": parameters.library_cflags,
                "CXXFLAGS": parameters.library_cxxflags,
            },
            unchosen=_UNCHOSEN_COMPILER_SETTINGS,
        ),
    }


def _container_harness_environment(parameters: BuildParameters) -> dict[str, str]:
    """The harness-compile settings to forward into the container.

    The default harness flags are dropped rather than forwarded: replacing CFLAGS with just
    `-fsanitize=fuzzer` would throw away the sanitizer configuration `compile` adds. A
    genuinely chosen value still forwards, since `compile` appends SANITIZER_FLAGS on top of
    whatever CFLAGS it inherits rather than replacing them.
    """
    return _chosen_settings(
        {
            "CC": parameters.cc,
            "CXX": parameters.cxx,
            "CFLAGS": parameters.harness_cflags,
            "CXXFLAGS": parameters.harness_cxxflags,
        },
        unchosen={
            **_UNCHOSEN_COMPILER_SETTINGS,
            "CFLAGS": _UNCHOSEN_HARNESS_FLAGS,
            "CXXFLAGS": _UNCHOSEN_HARNESS_FLAGS,
        },
    )


_TOKEN_PATH_PATTERN = re.compile(r"^(?P<flag>--?[A-Za-z][A-Za-z-]*=?)?(?P<path>/src(?:/.*)?)$")


def _rewrite_path_field(value: str, host_prefix: str) -> str:
    if value == _CONTAINER_SRC_DIR or value.startswith(_CONTAINER_SRC_DIR + "/"):
        return host_prefix + value[len(_CONTAINER_SRC_DIR) :]
    return value


def _rewrite_argument_token(token: str, host_prefix: str) -> str:
    """Rewrite a single compiler-argument token whose path is /src or /src/... glued
    directly after a short flag (-I, -L, -isystem, --sysroot=, ...)."""
    match = _TOKEN_PATH_PATTERN.match(token)
    if match is None:
        return token
    flag = match.group("flag") or ""
    path = match.group("path")
    return flag + host_prefix + path[len(_CONTAINER_SRC_DIR) :]


def _rewrite_directory_field(entry: dict, host_prefix: str) -> bool:
    directory = entry.get("directory")
    if not isinstance(directory, str):
        return False
    rewritten = _rewrite_path_field(directory, host_prefix)
    if rewritten == directory:
        return False
    entry["directory"] = rewritten
    return True


def _rewrite_file_field(entry: dict, host_prefix: str) -> bool:
    file_path = entry.get("file")
    if not isinstance(file_path, str) or not file_path.startswith("/"):
        return False
    rewritten = _rewrite_path_field(file_path, host_prefix)
    if rewritten == file_path:
        return False
    entry["file"] = rewritten
    return True


def _rewrite_arguments_field(entry: dict, host_prefix: str) -> bool:
    arguments = entry.get("arguments")
    if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
        return False
    new_arguments = [_rewrite_argument_token(a, host_prefix) for a in arguments]
    if new_arguments == arguments:
        return False
    entry["arguments"] = new_arguments
    return True


def _rewrite_command_field(entry: dict, host_prefix: str) -> bool:
    command = entry.get("command")
    if not isinstance(command, str):
        return False
    tokens = shlex.split(command)
    new_tokens = [_rewrite_argument_token(t, host_prefix) for t in tokens]
    if new_tokens == tokens:
        return False
    entry["command"] = shlex.join(new_tokens)
    return True


def _rewrite_compile_commands_entry(entry: dict, host_prefix: str) -> bool:
    """Rewrite one compile_commands.json entry's directory/file/command/arguments
    fields in place. Returns whether anything changed."""
    field_rewriters = (
        _rewrite_directory_field,
        _rewrite_file_field,
        _rewrite_arguments_field,
        _rewrite_command_field,
    )
    # Collected into a list first: any() would short-circuit and skip the later fields once
    # one rewrite succeeded.
    results = [rewrite(entry, host_prefix) for rewrite in field_rewriters]
    return any(results)


def rewrite_compile_commands_paths(compile_commands_path: Path, host_workdir: Path) -> None:
    """Rewrite the /src paths a docker-mounted build baked into compile_commands.json back
    to their host-side workdir path.

    Called from environments/gate.py, right after the container build that wrote those paths.

    The native feature extractor runs as a plain host subprocess and fatal-errors trying to
    chdir into a /src path that doesn't exist there. host_workdir <-> /src is a fixed
    prefix, so the substitution is exact.
    """
    host_prefix = str(host_workdir.resolve())
    entries = json.loads(compile_commands_path.read_text())

    changed = False
    for entry in entries:
        changed = _rewrite_compile_commands_entry(entry, host_prefix) or changed

    if changed:
        compile_commands_path.write_text(json.dumps(entries, indent=2))


class OssFuzzExecutor:
    """Runs each pipeline stage against the real OSS-Fuzz project layout in the workspace.

    Stateful per instance: run_library_build builds a run-scoped image from the workspace
    Dockerfile, used for the bind-mounted exploration build and for harness-link discovery's
    retry loop; run_harness_compile reuses it.

    Each stage's pass/fail comes from its own probe. The shared gate (check_build.sh, run in
    the container by check_build_in_container.sh) runs once, after the harness probe
    succeeds, with the workspace mounted at /src and its out/ mounted at the image's own
    $OUT=/out, so its artifacts — the harness binaries included — land on the host. Those
    mounts are also why a run finishes with run_from_scratch_docker_verification: a mounted
    gate can pass while the Dockerfile's own clone or apt layers are broken.
    """

    def __init__(
        self, *, base_image: str | None = None, bypass_scratch_validation: bool = False
    ) -> None:
        self._image_tag: str | None = None
        self._project_name: str | None = None
        self._base_image = base_image
        self._bypass_scratch_validation = bypass_scratch_validation

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
        self,
        analysis: AnalysisResult,
        workdir: Path,
        *,
        timeout: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
        parameters: BuildParameters | None = None,
    ) -> BuildExplorationResult:
        from harnessbuddy.library_builder import workspace as workspace_layout
        from harnessbuddy.library_builder.build_parameters import BuildParameters
        from harnessbuddy.library_builder.exploration import explore, write_build_library_script
        from harnessbuddy.library_builder.models import BuildExplorationResult

        workdir = workdir.resolve()
        parameters = parameters or BuildParameters.defaults()
        self._project_name = analysis.project_name
        workspace_layout.materialize(
            workdir, analysis, parameters=parameters, base_image=self._base_image
        )
        _ensure_source_symlink(workdir, analysis)
        # Before _ensure_image: the Dockerfile's COPY of build_library.sh needs it on disk.
        write_build_library_script(analysis, workdir, parameters=parameters)

        try:
            self._ensure_image(workdir, analysis)
        except _ImageBuildError as exc:
            logger.debug("Failed to ensure image: %s", exc.result.stderr)
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
        # `docker run` does not forward the host process environment BuildParameters sets,
        # so the compiler settings are passed explicitly or they are silently ignored here.
        run = _docker_run_factory(
            self._image_tag, extra_mounts, _container_build_environment(parameters)
        )
        exploration_result = explore(
            analysis,
            workdir,
            timeout=timeout,
            environment=Environment.OSS_FUZZ,
            run=run,
            parameters=parameters,
        )
        if exploration_result.command:
            return exploration_result
        # No build was attempted (unidentified build system), so there is no command to
        # report. Point at the gate, which is what an agent's fix has to satisfy.
        return dataclasses.replace(
            exploration_result,
            command=verification.verification_command(
                workdir,
                environment=Environment.OSS_FUZZ,
                project_name=analysis.project_name,
            ),
        )

    def run_harness_compile(  # noqa: PLR0913 -- paths and build configuration are independent inputs
        self,
        install_dir: Path,
        workdir: Path,
        language: Language,
        *,
        extra_include_paths: list[str] | None = None,
        extra_library_paths: list[str] | None = None,
        parameters: BuildParameters | None = None,
        library_llm_used: bool = False,
    ) -> HarnessExplorationResult:
        from harnessbuddy.library_builder.build_parameters import BuildParameters
        from harnessbuddy.library_builder.environments import gate
        from harnessbuddy.library_builder.harness_explorer import explore_harness_compilation

        if self._image_tag is None or self._project_name is None:
            raise RuntimeError(
                "OssFuzzExecutor.run_harness_compile requires a prior successful "
                "run_library_build on this instance to establish the run-scoped image"
            )
        workdir = workdir.resolve()
        parameters = parameters or BuildParameters.defaults()
        # Discovery's retry loop execs directly against the already-built image, so it does
        # not pay for a `docker build` per attempt.
        run = _docker_run_factory(self._image_tag, [], _container_harness_environment(parameters))
        harness_result = explore_harness_compilation(
            install_dir,
            workdir,
            language,
            extra_include_paths=extra_include_paths,
            extra_library_paths=extra_library_paths,
            environment=Environment.OSS_FUZZ,
            run=run,
        )
        gated = gate.apply_to_harness_result(
            harness_result,
            workdir,
            environment=Environment.OSS_FUZZ,
            project_name=self._project_name,
            library_llm_used=library_llm_used,
            bypass_scratch_validation=self._bypass_scratch_validation,
        )
        if gated.llm_used is False and _is_environment_unavailable(gated.output):
            raise EnvironmentUnavailableError(
                f"Docker became unavailable during verification: {gated.stdout.strip()}",
                Environment.OSS_FUZZ,
            )
        return gated

    def _ensure_image(self, workdir: Path, analysis: AnalysisResult) -> None:
        """Build a run-scoped image from the workspace's real Dockerfile, for internal
        probing. The gate builds its own image from the same Dockerfile, to the same tag.

        Always invokes `docker build`, relying on Docker's layer cache when the Dockerfile
        is unchanged. Caching on the discovered package list instead would miss Dockerfile
        edits an agent made directly, leaving probes to run against a stale image.
        """
        tag = f"harnessbuddy-dev/{analysis.project_name}:latest"
        command = ["docker", "build", "-t", tag, "."]
        result = run_command(command, workdir, _IMAGE_BUILD_TIMEOUT_SECONDS)

        if result.exit_code != 0:
            if _is_environment_unavailable(result.output):
                raise EnvironmentUnavailableError(
                    f"Failed to build the oss-fuzz image: {result.stderr.strip()}",
                    Environment.OSS_FUZZ,
                )
            raise _ImageBuildError(command, result)

        self._require_compile(tag)
        self._image_tag = tag

    def _require_compile(self, image_tag: str) -> None:
        """Reject a base image that has no `compile`, before anything tries to build in it.

        Every stage enters the build through `compile`, and the workspace Dockerfile is
        written in terms of $SRC, so --base-image selects among OSS-Fuzz base images rather
        than accepting any image. Reported as unavailable rather than as a build failure: no
        edit a repair agent makes to build.sh puts `compile` into the image.
        """
        from harnessbuddy.library_builder.workspace import DEFAULT_BASE_IMAGE

        command = ["docker", "run", "--rm", "--entrypoint", "bash", image_tag]
        command += ["-c", "command -v compile"]
        if run_command(command, Path.cwd(), _AVAILABILITY_TIMEOUT_SECONDS).exit_code == 0:
            return
        raise EnvironmentUnavailableError(
            f"The base image behind {image_tag} has no `compile` on PATH, so it is not an "
            "OSS-Fuzz base image. --base-image selects among OSS-Fuzz base images (for example "
            f"{DEFAULT_BASE_IMAGE}); it cannot take an arbitrary image, because every "
            "verification stage enters the build through `compile` and the generated Dockerfile "
            "is written in terms of $SRC.",
            Environment.OSS_FUZZ,
        )
