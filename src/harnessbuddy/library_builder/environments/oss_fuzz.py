from __future__ import annotations

import dataclasses
import json
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


_CONTAINER_SRC_DIR = "/src"


def _docker_run_factory(image_tag: str, extra_mounts: list[Path]) -> Runner:
    """Build a Runner that executes command inside image_tag via `docker run --entrypoint bash`.

    cwd is bind-mounted at /src — the same path the OSS-Fuzz base image's own project
    layout uses ($SRC in a real base-builder image, and where workspace.write_dockerfile's
    baked-in `RUN git clone ... $SRC/src` / `COPY build_library.sh ...` puts things at
    `docker build` time) — rather than at cwd's own host path. This matters because real
    oss-fuzz tooling (the base image's own `compile` entrypoint, $LIB_FUZZING_ENGINE setup,
    etc.) is hardwired to /src; mounting anywhere else would make that tooling operate on
    the stale snapshot baked into the image instead of the live, host-editable workspace
    that harness-link discovery iteratively rewrites between attempts. Scripts referencing
    $SCRIPT_DIR-relative paths (the standard-layout convention exploration.py/
    harness_explorer.py generate) are unaffected, since $SCRIPT_DIR resolves to wherever
    the script actually runs from regardless of the literal path. extra_mounts covers the
    non-standard-layout case, where the analyzed source lives outside the workdir tree and
    is referenced by its own absolute host path — those keep mounting at that same path,
    unaffected by cwd's mount target moving to /src.
    Used only for internal probing (the exploration-time build, harness-link discovery
    attempts) — the atomic pass/fail gate always goes through verification.run_docker_verification
    instead (FR-002). FUZZING_LANGUAGE is set via the workspace Dockerfile's own ENV
    instruction (workspace.write_dockerfile), not passed here.
    """

    def run(command: list[str], cwd: Path, timeout: int) -> RunResult:
        docker_command = ["docker", "run", "--rm", "--entrypoint", "bash"]
        docker_command += ["-v", f"{cwd.resolve()}:{_CONTAINER_SRC_DIR}"]
        for mount in dict.fromkeys(str(p.resolve()) for p in extra_mounts):
            docker_command += ["-v", f"{mount}:{mount}"]
        docker_command += ["-w", _CONTAINER_SRC_DIR, image_tag, "-c", shlex.join(command)]
        logger.debug("Running docker command from %s:\n%s", str(cwd), shlex.join(docker_command))
        return run_command_streaming(docker_command, cwd, timeout)

    return run


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
    # any() would short-circuit and skip later fields once one rewrite succeeds — every
    # rewriter must run regardless, so results are collected in a list before combining.
    results = [rewrite(entry, host_prefix) for rewrite in field_rewriters]
    return any(results)


def _rewrite_compile_commands_paths(compile_commands_path: Path, host_workdir: Path) -> None:
    """Rewrite /src paths baked into compile_commands.json by the docker-mounted build
    back to their host-side workdir path.

    _docker_run_factory always mounts host_workdir at /src (_CONTAINER_SRC_DIR) inside
    the container, so cmake/bear bake container-absolute paths like "/src/build/foo"
    into directory/file/command/arguments. Left unrewritten, the native feature
    extractor — which runs as a plain host subprocess, never inside a container — fails
    with an LLVM fatal error when clang::tooling::ClangTool tries to chdir into a /src
    path that doesn't exist on the host. The mapping is a fixed, known prefix
    (host_workdir <-> /src), so a direct substitution is exact — no heuristics needed.
    """
    host_prefix = str(host_workdir.resolve())
    entries = json.loads(compile_commands_path.read_text())

    changed = False
    for entry in entries:
        changed = _rewrite_compile_commands_entry(entry, host_prefix) or changed

    if changed:
        compile_commands_path.write_text(json.dumps(entries, indent=2))


class OssFuzzExecutor:
    """Runs each pipeline stage against the real OSS-Fuzz project layout, materialized
    directly in the workspace as soon as its pieces are known (User Story 2), and gates
    pass/fail via the same check_docker_build.sh script the repair agent uses (FR-001,
    FR-002).

    Stateful per instance: run_library_build builds a run-scoped image tagged from the
    project name, used for internal probing (the bind-mounted exploration build,
    harness-link discovery attempts); run_harness_compile reuses that same image for its
    discovery loop. The actual pass/fail gate is a single
    fresh, from-scratch `docker build` + `compile` via check_docker_build.sh, run only once
    — at the end of run_harness_compile, when its probe already succeeded — since `compile`
    always runs build_library.sh then compile_harnesses.sh together (via build.sh); running
    the same gate again after run_library_build alone would silently redo the full library
    rebuild from scratch (the gate's container is never bind-mounted, so build_library.sh's
    skip-if-already-built check never applies to it) for no benefit. run_library_build's own
    pass/fail therefore comes from its bind-mounted probe alone. Either stage skips its
    reproduce-it command's actual execution when its own probe already failed: a failing
    probe already proves that from-scratch rebuild+recompile would fail identically.
    """

    def __init__(self) -> None:
        self._image_tag: str | None = None
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
        from harnessbuddy.library_builder.exploration import explore, write_build_library_script
        from harnessbuddy.library_builder.models import BuildExplorationResult

        workdir = workdir.resolve()
        self._project_name = analysis.project_name
        self._materialize_workspace(workdir, analysis)
        # Written before _ensure_image: the workspace Dockerfile's COPY of
        # build_library.sh (workspace.write_dockerfile) requires it to already exist.
        write_build_library_script(analysis, workdir, environment=Environment.OSS_FUZZ)

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
        run = _docker_run_factory(self._image_tag, extra_mounts)
        exploration_result = explore(
            analysis, workdir, timeout=timeout, environment=Environment.OSS_FUZZ, run=run
        )
        if exploration_result.compile_commands_path is not None:
            _rewrite_compile_commands_paths(exploration_result.compile_commands_path, workdir)
        if not exploration_result.command:
            # No real build attempt was made (e.g. unknown build system) — nothing for
            # the shared verification script to check.
            return exploration_result

        # Unlike LocalExecutor, the oss-fuzz atomic gate (check_docker_build.sh) always
        # runs `compile` in a fresh, unmounted container — build_library.sh's
        # skip-if-already-built check can never fire there (there's no persisted install/
        # to find), so running the gate once here and again after run_harness_compile
        # would redo the full library rebuild from scratch twice for no benefit. This
        # stage's pass/fail comes from the bind-mounted probe above; run_harness_compile's
        # own atomic gate is the one place `compile` actually runs, validating
        # build_library.sh and compile_harnesses.sh together in a single pass (matching
        # spec 011's original "one atomic check" intent). Report the reproduce-it command
        # for reference without paying to run it here.
        return dataclasses.replace(
            exploration_result,
            command=verification.docker_verification_command(workdir, analysis.project_name),
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
        # its internal retry loop (research.md #2) — no docker build per attempt. It does
        # invoke the base image's `compile` entrypoint each attempt (via
        # explore_harness_compilation's oss_fuzz branch) since that's what populates
        # $LIB_FUZZING_ENGINE; build_library.sh's skip-if-already-built check keeps that
        # cheap rather than requiring a fresh docker build.
        run = _docker_run_factory(self._image_tag, [])
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

        if not harness_result.succeeded:
            # Discovery above already exhausted its attempts against this install/
            # output — re-running the shared script would only reconfirm the same
            # failure at the cost of a second full `docker build` + `compile`. Report
            # the command a human/agent would use to verify a fix, without paying to
            # run it again.
            return dataclasses.replace(
                harness_result,
                command=verification.docker_verification_command(workdir, self._project_name),
            )

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

    def sync_artifacts_after_agent_fix(
        self, analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
    ) -> BuildExplorationResult:
        """Re-run the agent-fixed build_library.sh via a mounted docker run against the
        (possibly rebuilt) dev image, to populate workdir/install and capture
        compile_commands.json — neither of which check_docker_build.sh's unmounted
        docker run (used by the agent to verify its own fix) produces on the host.

        _ensure_image is called again here — always rebuilding, cheaply, via Docker's
        own layer cache — to pick up any Dockerfile edit the agent made (e.g. adding a
        package), covering both the common case (only build_library.sh changed, so this
        is a fast cache hit) and the rarer case where run_library_build's own image
        build never succeeded (so an agent that fixed the Dockerfile itself never left
        self._image_tag set). self._project_name (set unconditionally at the top of
        run_library_build, even when its own image build then fails) is the
        precondition check, not self._image_tag — that would incorrectly reject exactly
        the rarer case above.
        """
        from harnessbuddy.library_builder.exploration import explore
        from harnessbuddy.library_builder.models import BuildExplorationResult

        if self._project_name is None:
            raise RuntimeError(
                "OssFuzzExecutor.sync_artifacts_after_agent_fix requires a prior "
                "run_library_build on this instance to establish the workspace"
            )
        workdir = workdir.resolve()
        try:
            self._ensure_image(workdir, analysis)
        except _ImageBuildError as exc:
            # The agent's fix was already proven via check_docker_build.sh's own
            # from-scratch build; a hydration-only rebuild failing here doesn't change
            # that outcome, just means the harness stage may find install/ still empty.
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

        run = _docker_run_factory(self._image_tag, [])
        result = explore(
            analysis,
            workdir,
            timeout=timeout,
            environment=Environment.OSS_FUZZ,
            run=run,
            regenerate_script=False,
        )
        if result.compile_commands_path is not None:
            _rewrite_compile_commands_paths(result.compile_commands_path, workdir)
        return result

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
            write_default_fuzzer(harness_source_dir, analysis.language)
            compile_harnesses_path.write_text(_COMPILE_HARNESSES_SH_STUB)
            compile_harnesses_path.chmod(compile_harnesses_path.stat().st_mode | 0o111)

    def _ensure_image(self, workdir: Path, analysis: AnalysisResult) -> None:
        """Build a run-scoped image from the workspace's real Dockerfile — used for
        internal probing (the bind-mounted exploration build, harness-link discovery
        attempts), not the atomic pass/fail gate (research.md #1, #2).

        Always invokes `docker build`, relying on Docker's own layer cache for speed
        when the Dockerfile is unchanged. A prior version skipped this call whenever
        analysis.system_packages matched the last build, but that key can't see
        Dockerfile edits an agent makes directly (e.g. adding a package without also
        reporting it via agent_report.json) — probes then silently ran against a stale
        image missing the fix.
        """
        tag = f"harnessbuddy-dev/{analysis.project_name}:latest"
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
