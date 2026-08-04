from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from harnessbuddy.library_builder.timeouts import DEFAULT_BUILD_TIMEOUT_SECONDS

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

    @property
    def harness_probe_command(self) -> list[str]:
        """How to recompile the harnesses in this environment.

        The generated scripts are environment-independent; entering them is not. OSS-Fuzz
        goes through the base image's own `compile` rather than running
        compile_harnesses.sh directly, because `compile` is what assembles the build
        environment the image only half-provides: it resolves SANITIZER_FLAGS from
        SANITIZER_FLAGS_$SANITIZER and appends it to CFLAGS/CXXFLAGS, and its
        compile_libfuzzer step exports LIB_FUZZING_ENGINE=-fsanitize=fuzzer. The image's own
        ENV supplies neither — its CFLAGS carry no sanitizer at all and its
        LIB_FUZZING_ENGINE names /usr/lib/libFuzzingEngine.a, a deprecated archive that
        compile_libfuzzer is what creates. Skipping `compile` therefore either fails on the
        missing archive or, if only the engine flag is patched, silently links a harness with
        no instrumentation.

        `compile` runs build.sh, so it re-enters the library build as well as the harness
        compile. build_library.sh's skip-if-already-built guard is what keeps that cheap
        enough to do once per discovery attempt, and running the real link line is also what
        makes discovery faithful: sanitizer flags change which symbols come out undefined.

        This is why --base-image accepts OSS-Fuzz-style images only, and why
        OssFuzzExecutor checks for `compile` before probing.
        """
        if self is Environment.OSS_FUZZ:
            return ["bash", "-c", "compile"]
        return ["bash", "compile_harnesses.sh"]


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
        timeout: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
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

    def check_availability(self) -> None:
        """Raise EnvironmentUnavailableError if this environment cannot be used."""
        ...
