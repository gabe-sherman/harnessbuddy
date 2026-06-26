from __future__ import annotations

import json
from pathlib import Path

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.generation import OutputDirectoryExistsError, generate
from harnessbuddy.library_builder.models import BuildExplorationResult, BuildSystem

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "repos"
_FAKE_URL = "https://github.com/example/mylib.git"

_ALL_BUILD_SYSTEMS = [
    "cmake_repo",
    "meson_repo",
    "autotools_repo",
    "makefile_repo",
    "ninja_repo",
]

_EXPECTED_TOP_LEVEL_FILES = frozenset(
    {
        "project.yaml",
        "Dockerfile",
        "build.sh",
        "build_library.sh",
        "compile_harnesses.sh",
        "provenance.json",
    }
)


def _analysis(fixture_name: str, *, repo_ref: str | None = None):  # type: ignore[no-untyped-def]
    source = RepoSource(
        source_path=_FIXTURES / fixture_name,
        clone_url=_FAKE_URL,
        project_name="mylib",
        repo_ref=repo_ref,
    )
    return analyze(source)


# all files generated for each build system


@pytest.mark.parametrize("fixture_name", _ALL_BUILD_SYSTEMS)
def test_all_files_generated(fixture_name: str, tmp_path: Path) -> None:
    result = generate(_analysis(fixture_name), tmp_path)
    for name in _EXPECTED_TOP_LEVEL_FILES:
        assert (result.output_path / name).exists(), f"missing: {name}"
    assert (result.output_path / "harness_source" / "default_fuzzer.cc").exists()


# project.yaml


def test_project_yaml_cmake_language_c(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "project.yaml").read_text()
    assert content == f"homepage: {_FAKE_URL}\nlanguage: c\n"


def test_project_yaml_meson_language_cpp(tmp_path: Path) -> None:
    result = generate(_analysis("meson_repo"), tmp_path)
    content = (result.output_path / "project.yaml").read_text()
    assert content == f"homepage: {_FAKE_URL}\nlanguage: c++\n"


def test_project_yaml_contains_clone_url(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "project.yaml").read_text()
    assert _FAKE_URL in content


# Dockerfile — without repo_ref


def test_dockerfile_no_ref(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "Dockerfile").read_text()
    expected = (
        "FROM gcr.io/oss-fuzz-base/base-builder\n"
        f"RUN git clone {_FAKE_URL} $SRC/mylib\n"
        "COPY harness_source $SRC/harness_source\n"
        "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n"
        "WORKDIR $SRC/mylib\n"
    )
    assert content == expected


# Dockerfile — with repo_ref


def test_dockerfile_with_ref(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo", repo_ref="v1.3.2"), tmp_path)
    content = (result.output_path / "Dockerfile").read_text()
    expected = (
        "FROM gcr.io/oss-fuzz-base/base-builder\n"
        f"RUN git clone {_FAKE_URL} $SRC/mylib\n"
        "RUN git -C $SRC/mylib checkout v1.3.2\n"
        "COPY harness_source $SRC/harness_source\n"
        "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n"
        "WORKDIR $SRC/mylib\n"
    )
    assert content == expected


def test_dockerfile_no_ref_no_checkout_line(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "Dockerfile").read_text()
    assert "checkout" not in content


# build.sh


def test_build_sh_shebang_and_set(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "build.sh").read_text()
    assert content.startswith("#!/bin/bash\nset -euo pipefail\n")


def test_build_sh_calls_build_library(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "build.sh").read_text()
    assert '"$SRC/build_library.sh"' in content


def test_build_sh_calls_compile_harnesses(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "build.sh").read_text()
    assert '"$SRC/compile_harnesses.sh"' in content


def test_build_sh_deterministic(tmp_path: Path) -> None:
    content_a = generate(_analysis("cmake_repo"), tmp_path / "a").output_path
    content_b = generate(_analysis("cmake_repo"), tmp_path / "b").output_path
    assert (content_a / "build.sh").read_text() == (content_b / "build.sh").read_text()


# build_library.sh


def test_build_library_sh_shebang_and_set(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "build_library.sh").read_text()
    assert content.startswith("#!/bin/bash\nset -euo pipefail\n")


@pytest.mark.parametrize(
    ("fixture_name", "expected_bs"),
    [
        ("cmake_repo", "cmake"),
        ("meson_repo", "meson"),
        ("autotools_repo", "autotools"),
        ("makefile_repo", "makefile"),
        ("ninja_repo", "ninja"),
    ],
)
def test_build_library_sh_build_system_comment(
    fixture_name: str, expected_bs: str, tmp_path: Path
) -> None:
    result = generate(_analysis(fixture_name), tmp_path)
    content = (result.output_path / "build_library.sh").read_text()
    assert f"# build system: {expected_bs}" in content


def test_build_library_sh_creates_build_env(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "build_library.sh").read_text()
    assert "../build.env" in content
    assert "HB_INCLUDE_FLAGS" in content
    assert "HB_LIBRARY_FLAGS" in content


@pytest.mark.parametrize(
    ("fixture_name", "expected_cmd"),
    [
        ("cmake_repo", "cmake -B ../build"),
        ("meson_repo", "meson setup"),
        ("autotools_repo", "./configure"),
        ("makefile_repo", "make -j"),
        ("ninja_repo", "ninja"),
    ],
)
def test_build_library_sh_build_command(
    fixture_name: str, expected_cmd: str, tmp_path: Path
) -> None:
    result = generate(_analysis(fixture_name), tmp_path)
    content = (result.output_path / "build_library.sh").read_text()
    assert expected_cmd in content


@pytest.mark.parametrize("fixture_name", _ALL_BUILD_SYSTEMS)
def test_build_library_sh_include_flags(fixture_name: str, tmp_path: Path) -> None:
    result = generate(_analysis(fixture_name), tmp_path)
    content = (result.output_path / "build_library.sh").read_text()
    assert "-I../install/include" in content


@pytest.mark.parametrize("fixture_name", _ALL_BUILD_SYSTEMS)
def test_build_library_sh_library_flags(fixture_name: str, tmp_path: Path) -> None:
    result = generate(_analysis(fixture_name), tmp_path)
    content = (result.output_path / "build_library.sh").read_text()
    assert "-L../install/lib" in content


@pytest.mark.parametrize("fixture_name", _ALL_BUILD_SYSTEMS)
def test_build_library_sh_build_env_path(fixture_name: str, tmp_path: Path) -> None:
    result = generate(_analysis(fixture_name), tmp_path)
    content = (result.output_path / "build_library.sh").read_text()
    assert "../build.env" in content


# compile_harnesses.sh


def test_compile_harnesses_sh_shebang_and_set(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert content.startswith("#!/bin/bash\nset -euo pipefail\n")


def test_compile_harnesses_sh_sources_build_env(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert 'source "../build.env"' in content


def test_compile_harnesses_sh_build_env_path(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert "../build.env" in content


def test_compile_harnesses_sh_handles_c(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert "*.c)" in content
    assert '"$CC"' in content


def test_compile_harnesses_sh_handles_cpp(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert "*.cc|*.cpp|*.cxx)" in content
    assert '"$CXX"' in content


def test_compile_harnesses_sh_uses_lib_fuzzing_engine(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert "$LIB_FUZZING_ENGINE" in content


def test_compile_harnesses_sh_deterministic(tmp_path: Path) -> None:
    content_a = generate(_analysis("cmake_repo"), tmp_path / "a").output_path
    content_b = generate(_analysis("cmake_repo"), tmp_path / "b").output_path
    assert (content_a / "compile_harnesses.sh").read_text() == (
        content_b / "compile_harnesses.sh"
    ).read_text()


# default_fuzzer.cc


def test_default_fuzzer_defines_entry_point(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "harness_source" / "default_fuzzer.cc").read_text()
    assert "LLVMFuzzerTestOneInput" in content


def test_default_fuzzer_no_main(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "harness_source" / "default_fuzzer.cc").read_text()
    assert "main" not in content


# provenance.json


@pytest.mark.parametrize(
    ("fixture_name", "expected_bs"),
    [
        ("cmake_repo", "cmake"),
        ("meson_repo", "meson"),
        ("autotools_repo", "autotools"),
        ("makefile_repo", "makefile"),
        ("ninja_repo", "ninja"),
    ],
)
def test_provenance_build_system(fixture_name: str, expected_bs: str, tmp_path: Path) -> None:
    result = generate(_analysis(fixture_name), tmp_path)
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert provenance["build_system"] == expected_bs


def test_provenance_clone_url(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert provenance["clone_url"] == _FAKE_URL


def test_provenance_repo_ref_none(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert provenance["repo_ref"] is None


def test_provenance_repo_ref_set(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo", repo_ref="v1.3.2"), tmp_path)
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert provenance["repo_ref"] == "v1.3.2"


def test_provenance_cmake_build_files(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert "CMakeLists.txt" in provenance["build_files"]


def test_provenance_headers_relative_paths(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert all("/" in h or h.endswith(".h") for h in provenance["headers"])


def test_provenance_output_path_recorded(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert provenance["output_path"] == str(result.output_path)


# output directory already exists


def test_existing_output_dir_raises(tmp_path: Path) -> None:
    analysis = _analysis("cmake_repo")
    (tmp_path / analysis.project_name).mkdir()
    with pytest.raises(OutputDirectoryExistsError, match="already exists"):
        generate(analysis, tmp_path)


# GenerationResult fields


def test_generation_result_project_name(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    assert result.project_name == "mylib"


def test_generation_result_output_path(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    assert result.output_path == tmp_path / "mylib"


def test_generation_result_files_count(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    assert len(result.files) == 7


def test_generation_result_all_files_exist(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    assert all(f.is_file() for f in result.files)


# provenance.json — host_build_exploration


def _fake_exploration(succeeded: bool = True) -> BuildExplorationResult:
    return BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=succeeded,
        command=["cmake", "-B", "/tmp/build"],
        stdout="-- Configuring done",
        stderr="",
        exit_code=0 if succeeded else 1,
        duration_seconds=1.2,
    )


def test_provenance_no_exploration_omits_field(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path)
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert "host_build_exploration" not in provenance


def test_provenance_with_exploration_includes_field(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path, _fake_exploration())
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert "host_build_exploration" in provenance


def test_provenance_exploration_succeeded(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path, _fake_exploration(succeeded=True))
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert provenance["host_build_exploration"]["succeeded"] is True


def test_provenance_exploration_failed(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path, _fake_exploration(succeeded=False))
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert provenance["host_build_exploration"]["succeeded"] is False


def test_provenance_exploration_command(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path, _fake_exploration())
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert provenance["host_build_exploration"]["command"] == ["cmake", "-B", "/tmp/build"]


def test_provenance_exploration_exit_code(tmp_path: Path) -> None:
    result = generate(_analysis("cmake_repo"), tmp_path, _fake_exploration())
    provenance = json.loads((result.output_path / "provenance.json").read_text())
    assert provenance["host_build_exploration"]["exit_code"] == 0


# zlib-specific integration test (no network — uses local cmake_repo fixture)


def test_zlib_cmake_build_library_sh(tmp_path: Path) -> None:
    source = RepoSource(
        source_path=_FIXTURES / "cmake_repo",
        clone_url="https://github.com/madler/zlib.git",
        project_name="zlib",
        repo_ref="v1.3.2",
    )
    result = generate(analyze(source), tmp_path)
    content = (result.output_path / "build_library.sh").read_text()
    assert "cmake" in content
    assert "../build" in content
