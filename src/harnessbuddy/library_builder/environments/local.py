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

    Each stage's pass/fail comes from its own probe. The shared gate (check_build.sh, the same
    script a repair agent is told to run) runs once, after the harness probe succeeds: it runs
    build.sh and asserts the artifacts, so the published install/ and out/ come from a build
    the gate itself accepted. Whether it first deletes install/ and build/ depends on the lane
    -- see gate.apply_to_harness_result.
    """

    def __init__(
        self, *, base_image: str | None = None, bypass_scratch_validation: bool = False
    ) -> None:
        self._project_name: str | None = None
        # A local run never builds the generated Dockerfile, but the output ships it, so the
        # choice still has to be honoured here.
        self._base_image = base_image
        self._bypass_scratch_validation = bypass_scratch_validation

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
        from harnessbuddy.library_builder.build_parameters import (
            BuildParameters,
            compile_commands_capture_environment,
        )
        from harnessbuddy.library_builder.environments import verification
        from harnessbuddy.library_builder.exploration import explore

        workdir = workdir.resolve()
        parameters = parameters or BuildParameters.defaults()
        self._project_name = analysis.project_name
        # Before the build attempt, not after: the gate a repair agent is handed needs the
        # harness scaffold to exist whether or not a build was ever attempted.
        workspace.materialize(workdir, analysis, parameters=parameters, base_image=self._base_image)

        # Capture here and not only in the gate: CMake writes compile_commands.json during
        # configure, and the gate skips the library build whenever this build already ran cold.
        with parameters.library_environment(), compile_commands_capture_environment():
            result = explore(
                analysis,
                workdir,
                timeout=timeout,
                environment=Environment.LOCAL,
                parameters=parameters,
            )
        if result.command:
            return result
        # No build was attempted (unidentified build system), so there is no command to
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
        library_llm_used: bool = False,
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
            library_llm_used=library_llm_used,
            bypass_scratch_validation=self._bypass_scratch_validation,
        )
