from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.cli import build_library
from harnessbuddy.core.repos import RepoSource
from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.harness_explorer import explore_harness_compilation
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    BuildSystem,
    Language,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibSpec:
    url: str
    project_name: str
    build_system: BuildSystem
    builds_static: bool


@dataclass
class LibBuild:
    spec: LibSpec
    result: BuildExplorationResult
    workdir: Path
    source: Path


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

_STATIC_LIBS = [lib for lib in LIBS if lib.builds_static]
_DYN_LIBS = [lib for lib in LIBS if not lib.builds_static]
_AGENT = "claude"


def _make_analysis(build_system: BuildSystem, source_path: Path) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=build_system,
        build_files=[],
        headers=[],
        language=Language.C,
        clone_url="https://example.com/testlib.git",
        repo_ref=None,
    )


def _timeout() -> RunResult:
    return RunResult(stdout="", stderr="", exit_code=-1, duration_seconds=120.0)


def _require_cmake() -> None:
    if subprocess.run(["cmake", "--version"], capture_output=True).returncode != 0:
        pytest.skip("cmake not available")


def _build_lib(lib: LibSpec, tmp_path_factory: pytest.TempPathFactory) -> LibBuild:
    _require_cmake()
    src = tmp_path_factory.mktemp(f"{lib.project_name}_src")
    subprocess.run(
        ["git", "clone", "--depth=1", lib.url, str(src)],
        check=True,
        capture_output=True,
    )
    source = RepoSource(source_path=src, clone_url=lib.url, project_name=lib.project_name)
    workdir = tmp_path_factory.mktemp(f"{lib.project_name}_work")
    analysis = analyze(source)
    result = build_library(analysis, workdir, agent=_AGENT)
    install_dir = workdir / "install"
    result = explore_harness_compilation(install_dir, workdir, analysis.language)
    return LibBuild(spec=lib, result=result, workdir=workdir, source=src)


@pytest.fixture(scope="session")
def real_harness_build(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> LibBuild:
    return _build_lib(request.param, tmp_path_factory)


@pytest.mark.parametrize(
    "real_harness_build", _STATIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestStaticBuilds:
    # Assert no claude code usage here
    @pytest.fixture(autouse=True)
    def _forbid_agent(self) -> None:
        with patch(
            "harnessbuddy.library_builder.agents.invoke_library_builder_agent",
            side_effect=AssertionError(
                "invoke_library_builder_agent must not be called in static build tests"
            ),
        ):
            yield

    def test_library_builds(self, real_harness_build: LibBuild) -> None:
        result = real_harness_build.result
        assert result.succeeded, (
            f"{real_harness_build.spec.project_name} build failed:\n{result.stderr}"
        )

    def test_static_library_installed(self, real_harness_build: LibBuild) -> None:
        lib_dir = real_harness_build.workdir / "install" / "lib"
        assert any(lib_dir.glob("*.a")), f"no static library in {lib_dir}"

    def test_headers_installed(self, real_harness_build: LibBuild) -> None:
        include_dir = real_harness_build.workdir / "install" / "include"
        assert any(include_dir.iterdir()), f"no headers in {include_dir}"

    def test_build_library_script_written(self, real_harness_build: LibBuild) -> None:
        assert (real_harness_build.source / "build_library.sh").exists()

    def test_build_env_written(self, real_harness_build: LibBuild) -> None:
        assert (real_harness_build.workdir / "build.env").exists()

    def test_build_env_has_include_flags(self, real_harness_build: LibBuild) -> None:
        workdir = real_harness_build.workdir
        env = (workdir / "build.env").read_text()
        assert f"-I{workdir.resolve()}/install/include" in env

    def test_build_env_has_library_flags(self, real_harness_build: LibBuild) -> None:
        workdir = real_harness_build.workdir
        env = (workdir / "build.env").read_text()
        assert f"-L{workdir.resolve()}/install/lib" in env

    def test_result_succeeded(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.succeeded is True

    def test_result_exit_code_zero(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.exit_code == 0

    def test_result_command_is_bash_script(self, real_harness_build: LibBuild) -> None:
        cmd = real_harness_build.result.command
        assert cmd[0] == "bash"
        assert Path(cmd[1]).name == "build_library.sh"


@pytest.mark.parametrize(
    "real_harness_build", _DYN_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestDynamicBuilds:
    def test_library_builds(self, real_harness_build: LibBuild) -> None:
        result = real_harness_build.result
        assert result.succeeded, (
            f"{real_harness_build.spec.project_name} build failed:\n{result.stderr}"
        )

    def test_static_library_installed(self, real_harness_build: LibBuild) -> None:
        lib_dir = real_harness_build.workdir / "install" / "lib"
        assert any(lib_dir.glob("*.a")), f"no static library in {lib_dir}"

    def test_headers_installed(self, real_harness_build: LibBuild) -> None:
        include_dir = real_harness_build.workdir / "install" / "include"
        assert any(include_dir.iterdir()), f"no headers in {include_dir}"

    def test_build_library_script_written(self, real_harness_build: LibBuild) -> None:
        assert (real_harness_build.source / "build_library.sh").exists()

    def test_build_env_written(self, real_harness_build: LibBuild) -> None:
        assert (real_harness_build.workdir / "build.env").exists()

    def test_build_env_has_include_flags(self, real_harness_build: LibBuild) -> None:
        workdir = real_harness_build.workdir
        env = (workdir / "build.env").read_text()
        assert f"-I{workdir.resolve()}/install/include" in env

    def test_build_env_has_library_flags(self, real_harness_build: LibBuild) -> None:
        workdir = real_harness_build.workdir
        env = (workdir / "build.env").read_text()
        assert f"-L{workdir.resolve()}/install/lib" in env

    def test_result_succeeded(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.succeeded is True

    def test_result_exit_code_zero(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.exit_code == 0

    def test_result_command_is_bash_script(self, real_harness_build: LibBuild) -> None:
        cmd = real_harness_build.result.command
        assert cmd[0] == "bash"
        assert Path(cmd[1]).name == "build_library.sh"


@pytest.mark.parametrize(
    "real_harness_build",
    [lib for lib in LIBS if lib.project_name == "zlib"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestZlibBuild:
    def test_builds(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.succeeded, (
            f"zlib build failed:\n{real_harness_build.result.stderr}"
        )

    def test_library_path_exists(self, real_harness_build: LibBuild) -> None:
        build_harness_source = real_harness_build.workdir / "build_harness.sh"
        assert build_harness_source.exists()
        with open(build_harness_source) as f:
            contents = f.read()
            assert "libz.a" in contents


@pytest.mark.parametrize(
    "real_harness_build",
    [lib for lib in LIBS if lib.project_name == "libtiff"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestLibtiffBuild:
    def test_builds(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.succeeded, (
            f"libtiff build failed:\n{real_harness_build.result.stderr}"
        )

    def test_system_package_inclusion(self, real_harness_build: LibBuild) -> None:
        build_harness_source = real_harness_build.workdir / "build_harness.sh"
        assert build_harness_source.exists()
        with open(build_harness_source) as f:
            contents = f.read()
            assert "-llzma" in contents
