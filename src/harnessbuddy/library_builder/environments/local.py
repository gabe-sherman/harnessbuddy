from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.timeouts import DEFAULT_BUILD_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from harnessbuddy.library_builder.build_parameters import BuildParameters
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
        Language,
    )


class LocalExecutor:
    """Runs each pipeline stage as a host subprocess.

    Each stage's pass/fail comes from its own probe. The shared gate
    (agents/scripts/check_build.sh, the same script a repair agent is told to run) runs
    once, after the harness probe succeeds: it rebuilds the library from nothing and
    asserts the artifacts, which is what makes the published install/ and out/ the product
    of a verified from-scratch build rather than of the probe's incremental one.
    """

    def __init__(self, *, base_image: str | None = None) -> None:
        self._project_name: str | None = None
        # Only reaches the generated Dockerfile, which a local run never builds — but the
        # output ships it, so the choice has to be honoured here too.
        self._base_image = base_image

    def check_availability(self) -> None:
        """The host is always available; nothing to check."""

    def run_library_build(
        self,
        analysis: AnalysisResult,
        workdir: Path,
        *,
        timeout: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
        parameters: BuildParameters | None = None,
    ) -> BuildExplorationResult:
        from harnessbuddy.library_builder import workspace
        from harnessbuddy.library_builder.build_parameters import BuildParameters
        from harnessbuddy.library_builder.environments import verification
        from harnessbuddy.library_builder.exploration import explore

        workdir = workdir.resolve()
        parameters = parameters or BuildParameters.defaults()
        self._project_name = analysis.project_name
        # Before the build attempt, not after: a repair agent for an unidentified build
        # system is handed the same gate command as any other, and that gate needs the
        # harness scaffold to exist whether or not a build was ever attempted.
        workspace.materialize(workdir, analysis, parameters=parameters, base_image=self._base_image)

        with parameters.library_environment():
            result = explore(
                analysis,
                workdir,
                timeout=timeout,
                environment=Environment.LOCAL,
                parameters=parameters,
            )
        if result.command:
            return result
        # No build was attempted (an unidentified build system), so there is no command to
        # report. Point at the gate, which is what an agent's fix has to satisfy.
        return dataclasses.replace(
            result,
            command=verification.verification_command(
                workdir, environment=Environment.LOCAL, project_name=analysis.project_name
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
    ) -> HarnessExplorationResult:
        from harnessbuddy.library_builder.build_parameters import BuildParameters
        from harnessbuddy.library_builder.environments import gate
        from harnessbuddy.library_builder.harness_explorer import explore_harness_compilation

        workdir = workdir.resolve()
        parameters = parameters or BuildParameters.defaults()
        with parameters.harness_environment():
            harness_result = explore_harness_compilation(
                install_dir,
                workdir,
                language,
                extra_include_paths=extra_include_paths,
                extra_library_paths=extra_library_paths,
                environment=Environment.LOCAL,
            )
        return gate.apply_to_harness_result(
            harness_result,
            workdir,
            environment=Environment.LOCAL,
            project_name=self._project_name or workdir.name,
        )
