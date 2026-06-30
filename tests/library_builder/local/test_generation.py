from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.local.generation import generate_local

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
    result = generate_local(_analysis(fixture_name), tmp_path / "out")
    assert (result.output_path / "setup.sh").exists()
    assert (result.output_path / "build_library.sh").exists()
    assert (result.output_path / "build_harness.sh").exists()
    assert any((result.output_path / "harness_src").glob("default_fuzzer.*"))


def test_generation_result_output_path(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = generate_local(_analysis("cmake_repo"), out)
    assert result.output_path == out


def test_generation_result_project_name(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out")
    assert result.project_name == "mylib"


def test_generation_result_all_files_exist(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out")
    assert all(f.is_file() for f in result.files)


# setup.sh — conditional checkout behavior


def test_setup_sh_git_clone_url(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out")
    content = (result.output_path / "setup.sh").read_text()
    assert f"git clone {_FAKE_URL}" in content


def test_setup_sh_no_checkout_without_ref(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out")
    content = (result.output_path / "setup.sh").read_text()
    assert "checkout" not in content


def test_setup_sh_checkout_with_ref(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo", repo_ref="v1.3.2"), tmp_path / "out")
    content = (result.output_path / "setup.sh").read_text()
    assert "checkout v1.3.2" in content


# setup.sh — install commands


def test_setup_sh_apt_when_system_packages_set(tmp_path: Path) -> None:
    analysis = _analysis("cmake_repo")
    analysis.system_packages = ["libssl-dev", "libzstd-dev"]
    with patch("sys.platform", "linux"):
        result = generate_local(analysis, tmp_path / "out")
    content = (result.output_path / "setup.sh").read_text()
    assert "apt-get install -y --no-install-recommends libssl-dev libzstd-dev" in content
    assert "brew" not in content


def test_setup_sh_brew_when_darwin_and_brew_packages_set(tmp_path: Path) -> None:
    analysis = _analysis("cmake_repo")
    with patch("sys.platform", "darwin"):
        result = generate_local(analysis, tmp_path / "out", brew_packages=["openssl", "zstd"])
    content = (result.output_path / "setup.sh").read_text()
    assert "brew install openssl zstd" in content
    assert "apt-get" not in content


def test_setup_sh_apt_when_darwin_but_no_brew_packages(tmp_path: Path) -> None:
    analysis = _analysis("cmake_repo")
    analysis.system_packages = ["libssl-dev"]
    with patch("sys.platform", "darwin"):
        result = generate_local(analysis, tmp_path / "out", brew_packages=[])
    content = (result.output_path / "setup.sh").read_text()
    assert "apt-get install" in content


def test_setup_sh_todo_comment_when_no_packages(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out")
    content = (result.output_path / "setup.sh").read_text()
    assert "TODO: install build dependencies" in content
    assert "apt-get" not in content
    assert "brew" not in content


# default_fuzzer.c


def test_default_fuzzer_c_no_main(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out")
    fuzzer = next((result.output_path / "harness_src").glob("default_fuzzer.*"))
    assert "main" not in fuzzer.read_text()


# error paths


def test_existing_output_dir_raises(tmp_path: Path) -> None:
    output_path = tmp_path / "out"
    output_path.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        generate_local(_analysis("cmake_repo"), output_path)
