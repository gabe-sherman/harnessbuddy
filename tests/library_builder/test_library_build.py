from __future__ import annotations

import dataclasses
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from harnessbuddy.cli import build_harness, build_library
from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.environments.base import Environment, EnvironmentUnavailableError
from harnessbuddy.library_builder.environments.local import LocalExecutor
from harnessbuddy.library_builder.environments.oss_fuzz import OssFuzzExecutor
from harnessbuddy.library_builder.models import (
    BuildExplorationResult,
    BuildSystem,
    HarnessExplorationResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibSpec:
    url: str
    project_name: str
    build_system: BuildSystem
    builds_deterministically: bool


@dataclass
class LibBuild:
    spec: LibSpec
    environment: Environment
    library_result: BuildExplorationResult
    harness_result: HarnessExplorationResult
    workdir: Path
    source: Path
    logs_dir: Path


LIBS = [
    # cmake
    LibSpec("https://github.com/madler/zlib.git", "zlib", BuildSystem.CMAKE, True),
    LibSpec("https://gitlab.com/libtiff/libtiff.git", "libtiff", BuildSystem.CMAKE, True),
    LibSpec("https://github.com/HDFGroup/hdf5.git", "hdf5", BuildSystem.CMAKE, True),
    LibSpec(
        "https://github.com/c-ares/c-ares.git", "c-ares", BuildSystem.CMAKE, False
    ),  # non-canonical static flag (-DCARES_STATIC)
    LibSpec(
        "https://github.com/curl/curl.git", "curl", BuildSystem.CMAKE, False
    ),  # requires libpsl
    LibSpec("https://github.com/fukuchi/libqrencode.git", "libqrencode", BuildSystem.CMAKE, False),
    # make
    LibSpec("https://github.com/lz4/lz4.git", "lz4", BuildSystem.MAKEFILE, True),
    # autotools
    LibSpec(
        "https://github.com/libimobiledevice/libplist", "libplist", BuildSystem.AUTOTOOLS, True
    ),
    LibSpec("https://github.com/gpac/gpac.git", "gpac", BuildSystem.AUTOTOOLS, True),
    LibSpec("https://github.com/file/file.git", "file", BuildSystem.AUTOTOOLS, True),
    LibSpec("https://github.com/mm2/Little-CMS.git", "lcms", BuildSystem.AUTOTOOLS, True),
    # meson
    LibSpec(
        "https://gitlab.gnome.org/GNOME/tinysparql.git", "tinysparql", BuildSystem.MESON, False
    ),  # requires external deps
    LibSpec(
        "https://github.com/rauc/rauc.git", "rauc", BuildSystem.MESON, False
    ),  # requires dbus-1
]

_DETERMINISTIC_LIBS = [lib for lib in LIBS if lib.builds_deterministically]
_AGENTIC_LIBS = [lib for lib in LIBS if not lib.builds_deterministically]
_AGENT = "claude"

# Smoke subset: a fast cross-section of build systems that runs by default. It draws from both
# lanes on purpose — zlib and libplist build deterministically, c-ares and libqrencode need the
# repair agent — so `pytest -q` exercises agent repair rather than only the happy path. That
# makes the default run cost real LLM tokens and need a Docker daemon. The rest of
# _DETERMINISTIC_LIBS is real but slower coverage, opt-in via `-m build_matrix`.
_SMOKE_PROJECTS = {"zlib", "c-ares", "libplist", "libqrencode"}
_SMOKE_LIBS = [
    lib for lib in _DETERMINISTIC_LIBS + _AGENTIC_LIBS if lib.project_name in _SMOKE_PROJECTS
]
_EXTENDED_DETERMINISTIC_LIBS = [
    lib for lib in _DETERMINISTIC_LIBS if lib.project_name not in _SMOKE_PROJECTS
]

# Round-robin the smoke libraries across environments, so both executors are exercised by
# default without doubling the number of real builds. Everything unlisted stays LOCAL.
_SMOKE_ENVIRONMENTS: dict[str, Environment] = {
    "zlib": Environment.OSS_FUZZ,
    "c-ares": Environment.OSS_FUZZ,
    "libplist": Environment.LOCAL,
    "libqrencode": Environment.LOCAL,
}


def _require_cmake() -> None:
    try:
        available = subprocess.run(["cmake", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        available = False
    if not available:
        pytest.skip("cmake not available")


def _select_executor(environment: Environment) -> LocalExecutor | OssFuzzExecutor:
    if environment is Environment.LOCAL:
        _require_cmake()
        return LocalExecutor()
    executor = OssFuzzExecutor()
    try:
        executor.check_availability()
    except EnvironmentUnavailableError as exc:
        pytest.skip(f"oss-fuzz environment unavailable: {exc}")
    return executor


def _build_lib(lib: LibSpec, tmp_path_factory: pytest.TempPathFactory) -> LibBuild:
    """Clone lib, then run the library build and the harness probe against it once, so both
    sets of tests share one real build instead of each re-cloning and rebuilding.

    Both stages use the same executor instance, since OssFuzzExecutor.run_harness_compile needs
    the run-scoped image only its own prior run_library_build establishes."""
    environment = _SMOKE_ENVIRONMENTS.get(lib.project_name, Environment.LOCAL)
    executor = _select_executor(environment)
    print(f"RUNNING BUILD LIB FOR {lib.project_name}")
    src = tmp_path_factory.mktemp(f"{lib.project_name}_src")
    subprocess.run(
        ["git", "clone", "--depth=1", lib.url, str(src)],
        check=True,
        capture_output=True,
    )
    source = RepoSource(source_path=src, clone_url=lib.url, project_name=lib.project_name)
    workdir = tmp_path_factory.mktemp(f"{lib.project_name}_work")
    logs_dir = workdir / "logs"
    analysis = analyze(source)
    library_result = build_library(analysis, workdir, executor, agent=_AGENT, logs_dir=logs_dir)
    install_dir = workdir / "install"
    harness_result = build_harness(
        analysis, install_dir, workdir, library_result, executor, agent=_AGENT, logs_dir=logs_dir
    )
    return LibBuild(
        spec=lib,
        environment=environment,
        library_result=library_result,
        harness_result=harness_result,
        workdir=workdir,
        source=src,
        logs_dir=logs_dir,
    )


@pytest.fixture(scope="session")
def real_library_build(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> LibBuild:
    return _build_lib(request.param, tmp_path_factory)


@pytest.fixture(scope="session")
def broken_cmake_build(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[BuildExplorationResult, Path]:
    _require_cmake()
    src = tmp_path_factory.mktemp("broken_cmake_src")
    (src / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\n"
        "project(broken)\n"
        "find_package(NonExistentPackage_abc123 REQUIRED)\n"
    )
    (src / "stub.h").write_text("#pragma once\n")
    workdir = tmp_path_factory.mktemp("broken_cmake_work")
    analysis = analyze(RepoSource(src, "https://example.com", "broken", None))
    result = build_library(analysis, workdir, LocalExecutor())
    return result, workdir


# library builds succeed and install artifacts
#
# These checks assert the outcome, not how it was reached: every suite below runs with the
# repair agent enabled, so a library may build deterministically or be repaired on the way.
# `builds_deterministically` records only which of the two is expected, and drives which
# libraries land in the opt-in agentic matrix.


class _LibraryBuildChecks:
    def test_library_builds(self, real_library_build: LibBuild) -> None:
        result = real_library_build.library_result
        assert result.succeeded, (
            f"{real_library_build.spec.project_name} build failed:\n{result.stderr}"
        )

    def test_static_library_installed(self, real_library_build: LibBuild) -> None:
        lib_dir = real_library_build.workdir / "install" / "lib"
        assert any(lib_dir.glob("*.a")), f"no static library in {lib_dir}"

    def test_headers_installed(self, real_library_build: LibBuild) -> None:
        include_dir = real_library_build.workdir / "install" / "include"
        assert any(include_dir.iterdir()), f"no headers in {include_dir}"

    def test_build_library_script_written(self, real_library_build: LibBuild) -> None:
        assert (real_library_build.workdir / "build_library.sh").exists()

    def test_result_command_is_the_build_invocation(self, real_library_build: LibBuild) -> None:
        """The library stage's pass/fail comes from its own probe, so that is the command it
        reports. The gate runs once, after the harness stage."""
        cmd = real_library_build.library_result.command
        assert cmd[0] in {"bash", "bear", "claude"}
        assert "build_library.sh" in cmd

    def test_deterministic_library_build_phase_log_written(
        self, real_library_build: LibBuild
    ) -> None:
        """A real build's full raw output stays retrievable from its per-phase log file,
        whether or not it also streamed live to the console.

        The deterministic phase always runs and always logs, even when it fails and the agent
        goes on to repair the build."""
        log_path = real_library_build.logs_dir / "deterministic_library_build.log"
        assert log_path.exists()
        assert log_path.stat().st_size > 0


@pytest.mark.smoke
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _SMOKE_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestSmokeLibraryBuilds(_LibraryBuildChecks):
    """Runs by default (pytest -q) — a fast cross-section of build systems.

    Deliberately mixes libraries that build deterministically with ones that need the repair
    agent, so the default run covers both lanes end to end."""


@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build",
    _EXTENDED_DETERMINISTIC_LIBS,
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestExtendedDeterministicLibraryBuilds(_LibraryBuildChecks):
    """Opt-in only (`-m build_matrix`) — the rest of the deterministic-build matrix."""


# harness-compile probe succeeds against those same install artifacts


class _HarnessBuildChecks:
    def test_harness_compiles(self, real_library_build: LibBuild) -> None:
        result = real_library_build.harness_result
        assert result.succeeded, (
            f"{real_library_build.spec.project_name} harness probe failed:\n{result.stderr}"
        )


@pytest.mark.smoke
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _SMOKE_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestSmokeHarnessBuilds(_HarnessBuildChecks):
    """Runs by default (pytest -q)."""


@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build",
    _EXTENDED_DETERMINISTIC_LIBS,
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestExtendedDeterministicHarnessBuilds(_HarnessBuildChecks):
    """Opt-in only (`-m build_matrix`)."""


# libraries whose deterministic build is expected to fail, so the repair agent has to fix them


@pytest.mark.agentic
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _AGENTIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestAgenticLibraryBuilds:
    def test_library_builds(self, real_library_build: LibBuild) -> None:
        result = real_library_build.library_result
        assert result.succeeded, (
            f"{real_library_build.spec.project_name} build failed:\n{result.stderr}"
        )

    def test_static_library_installed(self, real_library_build: LibBuild) -> None:
        lib_dir = real_library_build.workdir / "install" / "lib"
        assert any(lib_dir.glob("*.a")), f"no static library in {lib_dir}"

    def test_headers_installed(self, real_library_build: LibBuild) -> None:
        include_dir = real_library_build.workdir / "install" / "include"
        assert any(include_dir.iterdir()), f"no headers in {include_dir}"

    def test_build_library_script_written(self, real_library_build: LibBuild) -> None:
        assert (real_library_build.workdir / "build_library.sh").exists()


@pytest.mark.agentic
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _AGENTIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestAgenticHarnessBuilds:
    def test_harness_compiles(self, real_library_build: LibBuild) -> None:
        result = real_library_build.harness_result
        assert result.succeeded, (
            f"{real_library_build.spec.project_name} harness probe failed:\n{result.stderr}"
        )


@pytest.mark.agentic
@pytest.mark.parametrize(
    "real_library_build",
    [lib for lib in LIBS if lib.project_name == "curl"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestCurlBuild:
    def test_llm_used_to_fix_missing_libpsl(self, real_library_build: LibBuild) -> None:
        assert real_library_build.library_result.llm_used, (
            "build succeeded with no LLM usage, this indicates something weird is happening"
        )


@pytest.mark.smoke
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build",
    [lib for lib in LIBS if lib.project_name == "zlib"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestZlibBuild:
    def test_discovered_archive_reaches_the_harness_compiler(
        self, real_library_build: LibBuild
    ) -> None:
        compiler = real_library_build.workdir / "compile_harness.sh"
        assert compiler.exists()
        assert "libz.a" in compiler.read_text()


@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build",
    [lib for lib in LIBS if lib.project_name == "libtiff"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestLibtiffBuild:
    def test_transitive_link_flag_reaches_the_harness_compiler(
        self, real_library_build: LibBuild
    ) -> None:
        compiler = real_library_build.workdir / "compile_harness.sh"
        assert compiler.exists()
        assert "-llzma" in compiler.read_text()


# real build failure


def test_broken_cmake_not_succeeded(broken_cmake_build: tuple) -> None:
    result, _ = broken_cmake_build
    assert result.succeeded is False


def test_broken_cmake_exit_code_nonzero(broken_cmake_build: tuple) -> None:
    result, _ = broken_cmake_build
    assert result.exit_code != 0


# --library-configure-arg against a real library
#
# c-ares has builds_deterministically=False because -DBUILD_SHARED_LIBS=OFF alone does not make
# it install a static library, so the deterministic build has nothing to link and the agent is
# called. Its
# own -DCARES_STATIC is the switch that does, which makes it an honest test of whether a
# caller-supplied configure option reaches the configure step: supply it and the same library
# builds deterministically, with no agent involved.


@pytest.mark.build_matrix
class TestConfigureArgsAgainstARealLibrary:
    _SPEC = next(lib for lib in LIBS if lib.project_name == "c-ares")
    _STATIC_FLAG = "-DCARES_STATIC=ON"

    @pytest.fixture(scope="class")
    def source(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        src = tmp_path_factory.mktemp("cares_src")
        subprocess.run(
            ["git", "clone", "--depth=1", self._SPEC.url, str(src)],
            check=True,
            capture_output=True,
        )
        return src

    def _build(self, source: Path, workdir: Path, *configure_args: str) -> BuildExplorationResult:
        """Build through the executor directly, so no repair agent can mask the outcome."""
        from harnessbuddy.library_builder.build_parameters import BuildParameters

        analysis = analyze(
            RepoSource(source_path=source, clone_url=self._SPEC.url, project_name="c-ares")
        )
        assert analysis.build_system is BuildSystem.CMAKE
        parameters = dataclasses.replace(
            BuildParameters.defaults(), library_configure_args=configure_args
        )
        return LocalExecutor().run_library_build(analysis, workdir, parameters=parameters)

    def test_the_build_has_no_static_library_without_the_configure_arg(
        self, source: Path, tmp_path: Path
    ) -> None:
        """The premise the positive case rests on. cmake succeeds and installs headers either
        way; what is missing is the *.a the harness has to link, which is why this shows up as a
        failed build rather than a failed configure."""
        result = self._build(source, tmp_path / "without")

        assert result.succeeded is False
        assert not list((tmp_path / "without" / "install" / "lib").glob("*.a"))
        assert "no static libraries" in result.output

    def test_the_configure_arg_makes_the_same_library_build(
        self, source: Path, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "with"
        result = self._build(source, workdir, self._STATIC_FLAG)

        assert result.succeeded is True, result.output[-2000:]
        assert [path.name for path in (workdir / "install" / "lib").glob("*.a")] == ["libcares.a"]
        assert result.llm_used is False

    def test_the_shipped_script_carries_the_configure_arg(
        self, source: Path, tmp_path: Path
    ) -> None:
        """The option has to survive into build_library.sh, not just into this run's cmake
        invocation: that script is what the gate re-runs from nothing and what generation
        publishes."""
        workdir = tmp_path / "shipped"
        self._build(source, workdir, self._STATIC_FLAG)

        script = (workdir / "build_library.sh").read_text()
        assert f"CONFIGURE_ARGS=('{self._STATIC_FLAG}')" in script
        assert '-DBUILD_SHARED_LIBS=OFF "${CONFIGURE_ARGS[@]}"' in script
