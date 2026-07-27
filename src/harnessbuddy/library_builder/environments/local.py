from __future__ import annotations

import dataclasses
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from harnessbuddy.library_builder.environments.base import Environment

if TYPE_CHECKING:
    from harnessbuddy.library_builder.build_parameters import BuildParameters
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
        Language,
    )


class LocalExecutor:
    """Runs each pipeline stage as a host subprocess, gated by the same
    agents/scripts/check_local_build.sh script the repair agent uses (FR-001, FR-003).
    That gate only runs after its stage's own probe succeeds — a failing probe already
    proves the shared script would fail identically, so it's skipped rather than re-run."""

    def check_availability(self) -> None:
        """The host is always available; nothing to check."""

    def run_library_build(
        self,
        analysis: AnalysisResult,
        workdir: Path,
        *,
        timeout: int = 300,
        parameters: BuildParameters | None = None,
    ) -> BuildExplorationResult:
        from harnessbuddy.library_builder.build_parameters import BuildParameters
        from harnessbuddy.library_builder.environments import verification
        from harnessbuddy.library_builder.exploration import explore
        from harnessbuddy.library_builder.scripts import (
            build_harness_script,
            build_harnesses_script,
            write_default_fuzzer,
        )

        workdir = workdir.resolve()
        parameters = parameters or BuildParameters.from_args(object())
        with parameters.library_environment():
            exploration_result = explore(
                analysis, workdir, timeout=timeout, environment=Environment.LOCAL
            )
        if not exploration_result.command:
            # No real build attempt was made (e.g. unknown build system) — nothing for
            # the shared verification script to check.
            return exploration_result

        # The verifier runs both scripts in a reused workspace, so refresh them instead
        # of trusting artifacts emitted by an earlier HarnessBuddy version or invocation.
        harness_src_dir = workdir / "harness_src"
        harness_src_dir.mkdir(exist_ok=True)
        write_default_fuzzer(harness_src_dir, analysis.language)
        compiler_path = workdir / "compile_harness.sh"
        compiler_path.write_text(
            build_harness_script(
                None,
                local_cflags=parameters.harness_cflags,
                local_cxxflags=parameters.harness_cxxflags,
            )
        )
        compiler_path.chmod(
            compiler_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        batch_path = workdir / "compile_harnesses.sh"
        batch_path.write_text(
            build_harnesses_script(harness_dir_name="harness_src", oss_fuzz=False)
        )
        batch_path.chmod(batch_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        if not exploration_result.succeeded:
            # The probe above already failed against this exact build_library.sh —
            # re-running the shared script would only reconfirm the same failure. Report
            # the command a human/agent would use to verify a fix, without paying to run
            # it again.
            return dataclasses.replace(
                exploration_result, command=verification.local_verification_command(workdir)
            )

        with parameters.harness_environment():
            result = verification.run_local_verification(workdir)
        return dataclasses.replace(
            exploration_result,
            succeeded=result.passed,
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=0 if result.passed else 1,
            duration_seconds=result.duration_seconds,
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
    ) -> HarnessExplorationResult:
        from harnessbuddy.library_builder.build_parameters import BuildParameters
        from harnessbuddy.library_builder.environments import verification
        from harnessbuddy.library_builder.harness_explorer import explore_harness_compilation

        workdir = workdir.resolve()
        parameters = parameters or BuildParameters.from_args(object())
        with parameters.harness_environment():
            harness_result = explore_harness_compilation(
                install_dir,
                workdir,
                language,
                extra_include_paths=extra_include_paths,
                extra_library_paths=extra_library_paths,
                environment=Environment.LOCAL,
            )
        if not harness_result.static_libs:
            # No install artifacts to link against — nothing for the shared
            # verification script to check.
            return harness_result

        if not harness_result.succeeded:
            # Discovery above already exhausted its attempts against this install/
            # output — re-running the shared script would only reconfirm the same
            # failure. Report the command a human/agent would use to verify a fix,
            # without paying to run it again.
            return dataclasses.replace(
                harness_result, command=verification.local_verification_command(workdir)
            )

        with parameters.harness_environment():
            result = verification.run_local_verification(workdir)
        return dataclasses.replace(
            harness_result,
            succeeded=result.passed,
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=0 if result.passed else 1,
            duration_seconds=result.duration_seconds,
        )

    def sync_artifacts_after_agent_fix(
        self, analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
    ) -> BuildExplorationResult:
        """Environment.LOCAL's repair-agent verification (check_local_build.sh) already
        ran build_library.sh directly on the host, so workdir/install is already correct
        — unlike Environment.OSS_FUZZ's unmounted docker equivalent, there's nothing to
        hydrate there. compile_commands.json is a different story: check_local_build.sh
        runs the script unwrapped (no bear), so it's never produced as a side effect of
        the agent's own verification. Recapture it via a scratch-BUILD_PREFIX rebuild
        that never touches the already-verified install/ (see
        exploration.recapture_compile_commands_after_agent_fix) — always succeeded=True
        here, since a failed recapture doesn't affect install/'s correctness, only
        whether compile_commands.json ends up available.
        """
        from harnessbuddy.library_builder.exploration import (
            recapture_compile_commands_after_agent_fix,
        )
        from harnessbuddy.library_builder.models import BuildExplorationResult

        workdir = workdir.resolve()
        compile_commands_path, compile_commands_error = recapture_compile_commands_after_agent_fix(
            analysis, workdir, timeout=timeout
        )
        return BuildExplorationResult(
            build_system=analysis.build_system,
            succeeded=True,
            command=[],
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.0,
            install_dir=workdir / "install",
            environment=Environment.LOCAL,
            compile_commands_path=compile_commands_path,
            compile_commands_error=compile_commands_error,
        )
