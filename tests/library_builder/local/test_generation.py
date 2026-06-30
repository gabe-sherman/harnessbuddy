from __future__ import annotations

from pathlib import Path

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.local.generation import generate_local
from harnessbuddy.library_builder.models import (
    BuildSystem,
    OutputDirectoryExistsError,
)

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "repos"
_FAKE_URL = "https://github.com/example/mylib.git"

_ALL_BUILD_SYSTEMS = [
    "cmake_repo",
    "meson_repo",
    "autotools_repo",
    "autotools_configure_repo",
    "autotools_autogen_repo",
    "makefile_repo",
]


def _analysis(fixture_name: str, *, repo_ref: str | None = None):  # type: ignore[no-untyped-def]
    source = RepoSource(
        source_path=_FIXTURES / fixture_name,
        clone_url=_FAKE_URL,
        project_name="mylib",
        repo_ref=repo_ref,
    )
    return analyze(source)


# all expected files present


@pytest.mark.parametrize("fixture_name", _ALL_BUILD_SYSTEMS)
def test_all_files_generated(fixture_name: str, tmp_path: Path) -> None:
    result = generate_local(_analysis(fixture_name), tmp_path)
    assert (result.output_path / "setup.sh").exists()
    assert (result.output_path / "build_library.sh").exists()
    assert (result.output_path / "build_harness.sh").exists()
    assert (result.output_path / "harness_src" / "default_fuzzer.c").exists()


def test_generation_result_output_path(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path)
    assert result.output_path == tmp_path / "mylib" / "output" / "local"


def test_generation_result_project_name(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path)
    assert result.project_name == "mylib"


def test_generation_result_all_files_exist(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path)
    assert all(f.is_file() for f in result.files)


# setup.sh — conditional checkout behavior


def test_setup_sh_git_clone_url(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "setup.sh").read_text()
    assert f"git clone {_FAKE_URL}" in content


def test_setup_sh_no_checkout_without_ref(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "setup.sh").read_text()
    assert "checkout" not in content


def test_setup_sh_checkout_with_ref(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo", repo_ref="v1.3.2"), tmp_path)
    content = (result.output_path / "setup.sh").read_text()
    assert "checkout v1.3.2" in content


# default_fuzzer.c


def test_default_fuzzer_c_no_main(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path)
    content = (result.output_path / "harness_src" / "default_fuzzer.c").read_text()
    assert "main" not in content


# error paths


def test_existing_output_dir_raises(tmp_path: Path) -> None:
    analysis = _analysis("cmake_repo")
    output_path = tmp_path / analysis.project_name / "output" / "local"
    output_path.mkdir(parents=True)
    with pytest.raises(OutputDirectoryExistsError):
        generate_local(analysis, tmp_path)
