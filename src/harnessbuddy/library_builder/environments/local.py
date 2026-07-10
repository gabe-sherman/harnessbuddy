from __future__ import annotations

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
    """Runs each pipeline stage as a host subprocess — today's only behavior."""

    def check_availability(self) -> None:
        """The host is always available; nothing to check."""

    def run_library_build(
        self, analysis: AnalysisResult, workdir: Path, *, timeout: int = 300
    ) -> BuildExplorationResult:
        from harnessbuddy.library_builder.exploration import explore

        return explore(analysis, workdir, timeout=timeout, environment=Environment.LOCAL)

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

        return explore_harness_compilation(
            install_dir,
            workdir,
            language,
            extra_include_paths=extra_include_paths,
            extra_library_paths=extra_library_paths,
            environment=Environment.LOCAL,
        )
