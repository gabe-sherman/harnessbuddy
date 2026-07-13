from __future__ import annotations

import dataclasses
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from harnessbuddy.library_builder.environments.base import Environment

if TYPE_CHECKING:
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
        self, analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
    ) -> BuildExplorationResult:
        from harnessbuddy.library_builder.environments import verification
        from harnessbuddy.library_builder.exploration import explore
        from harnessbuddy.library_builder.local.generation import _COMPILE_HARNESSES_SH_STUB
        from harnessbuddy.library_builder.scripts import write_default_fuzzer

        workdir = workdir.resolve()
        exploration_result = explore(
            analysis, workdir, timeout=timeout, environment=Environment.LOCAL
        )
        if not exploration_result.command:
            # No real build attempt was made (e.g. unknown build system) — nothing for
            # the shared verification script to check.
            return exploration_result

        stub_path = workdir / "compile_harnesses.sh"
        if not stub_path.exists():
            # The stub compiles whatever's in harness_src/ (research.md #3) — write the
            # real default fuzzer stub now so check_local_build.sh's out/ non-empty check
            # (agents/scripts/check_local_build.sh) has something to find even before
            # harness-link discovery ever runs. Written unconditionally (even when the
            # probe below already failed) since a later repair agent's own verification
            # run still needs it to exist.
            harness_src_dir = workdir / "harness_src"
            harness_src_dir.mkdir(exist_ok=True)
            write_default_fuzzer(harness_src_dir, analysis.language)
            stub_path.write_text(_COMPILE_HARNESSES_SH_STUB)
            stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        if not exploration_result.succeeded:
            # The probe above already failed against this exact build_library.sh —
            # re-running the shared script would only reconfirm the same failure. Report
            # the command a human/agent would use to verify a fix, without paying to run
            # it again.
            return dataclasses.replace(
                exploration_result, command=verification.local_verification_command(workdir)
            )

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

    def run_harness_compile(
        self,
        install_dir: Path,
        workdir: Path,
        language: Language,
        *,
        extra_include_paths: list[str] | None = None,
        extra_library_paths: list[str] | None = None,
    ) -> HarnessExplorationResult:
        from harnessbuddy.library_builder.environments import verification
        from harnessbuddy.library_builder.harness_explorer import explore_harness_compilation

        workdir = workdir.resolve()
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
        self,
        analysis: AnalysisResult,
        workdir: Path,  # noqa: ARG002 -- unused; signature must match the shared protocol
        *,
        timeout: int = 300,  # noqa: ARG002 -- unused; signature must match the shared protocol
    ) -> BuildExplorationResult:
        """No-op: Environment.LOCAL's repair-agent verification (check_local_build.sh)
        already ran build_library.sh directly on the host, so workdir/install is already
        correct — unlike Environment.OSS_FUZZ's unmounted docker equivalent, there's
        nothing to hydrate. Re-running the build here would only be a redundant rebuild,
        and a risky one: anything that made it behave differently from the agent's own
        verified run would overwrite already-correct artifacts with worse ones.
        """
        from harnessbuddy.library_builder.models import BuildExplorationResult

        return BuildExplorationResult(
            build_system=analysis.build_system,
            succeeded=True,
            command=[],
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=0.0,
            environment=Environment.LOCAL,
        )
