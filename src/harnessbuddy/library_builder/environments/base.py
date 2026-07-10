from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
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
        self, analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
    ) -> BuildExplorationResult: ...

    def run_harness_compile(
        self,
        install_dir: Path,
        workdir: Path,
        language: Language,
        *,
        extra_include_paths: list[str] | None = None,
        extra_library_paths: list[str] | None = None,
    ) -> HarnessExplorationResult: ...

    def check_availability(self) -> None:
        """Raise EnvironmentUnavailableError if this environment cannot be used."""
        ...
