from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from harnessbuddy.library_builder.build_parameters import BuildParameters
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
        Language,
    )


class Environment(Enum):
    LOCAL = "local"
    OSS_FUZZ = "oss-fuzz"


class EnvironmentUnavailableError(Exception):
    """The selected environment itself isn't usable (e.g. Docker daemon unreachable).

    Distinct from a build/stage failure: callers must not route this to agent
    fallback, since no amount of editing build scripts fixes an unavailable
    environment.
    """

    def __init__(self, message: str, environment: Environment) -> None:
        super().__init__(message)
        self.environment = environment


class EnvironmentExecutor(Protocol):
    """Runs the two generate pipeline stages in a specific target environment."""

    def run_library_build(
        self,
        analysis: AnalysisResult,
        workdir: Path,
        *,
        timeout: int = 300,
        parameters: BuildParameters | None = None,
    ) -> BuildExplorationResult: ...

    def run_harness_compile(  # noqa: PLR0913 -- paths and build configuration are independent inputs
        self,
        install_dir: Path,
        workdir: Path,
        language: Language,
        *,
        extra_include_paths: list[str] | None = None,
        extra_library_paths: list[str] | None = None,
        parameters: BuildParameters | None = None,
    ) -> HarnessExplorationResult: ...

    def sync_artifacts_after_agent_fix(
        self, analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
    ) -> BuildExplorationResult:
        """Re-run a repair agent's already-fixed build_library.sh (without regenerating
        it) to populate host-side artifacts (install/, compile_commands.json) that the
        agent's own out-of-band verification may not have produced. Best-effort: callers
        must not let a failure here regress an already-agent-verified success — it only
        affects what the next stage (harness compilation) can find on disk.
        """
        ...

    def check_availability(self) -> None:
        """Raise EnvironmentUnavailableError if this environment cannot be used."""
        ...
