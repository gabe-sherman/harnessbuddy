from __future__ import annotations

from pathlib import Path

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import UnsupportedRepositoryError, analyze
from harnessbuddy.library_builder.models import BuildSystem, Language

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "repos"
_FAKE_URL = "https://github.com/example/mylib.git"


def _repo(
    fixture_name: str,
    *,
    project_name: str = "mylib",
    repo_ref: str | None = None,
) -> RepoSource:
    return RepoSource(
        source_path=_FIXTURES / fixture_name,
        clone_url=_FAKE_URL,
        project_name=project_name,
        repo_ref=repo_ref,
    )


# cmake_repo


def test_cmake_build_system() -> None:
    assert analyze(_repo("cmake_repo")).build_system == BuildSystem.CMAKE


def test_cmake_build_files() -> None:
    result = analyze(_repo("cmake_repo"))
    assert any(f.name == "CMakeLists.txt" for f in result.build_files)


def test_cmake_headers() -> None:
    result = analyze(_repo("cmake_repo"))
    assert any(h.name == "mylib.h" for h in result.headers)


def test_cmake_language_c() -> None:
    assert analyze(_repo("cmake_repo")).language == Language.C


def test_cmake_no_warnings() -> None:
    assert analyze(_repo("cmake_repo")).warnings == []


# meson_repo


def test_meson_build_system() -> None:
    assert analyze(_repo("meson_repo")).build_system == BuildSystem.MESON


def test_meson_build_files() -> None:
    result = analyze(_repo("meson_repo"))
    assert any(f.name == "meson.build" for f in result.build_files)


def test_meson_headers() -> None:
    result = analyze(_repo("meson_repo"))
    assert any(h.suffix == ".hpp" for h in result.headers)


def test_meson_language_cpp() -> None:
    assert analyze(_repo("meson_repo")).language == Language.CPP


# autotools_repo


def test_autotools_build_system() -> None:
    assert analyze(_repo("autotools_repo")).build_system == BuildSystem.AUTOTOOLS


def test_autotools_build_files() -> None:
    result = analyze(_repo("autotools_repo"))
    assert any(f.name == "configure.ac" for f in result.build_files)


# makefile_repo


def test_makefile_build_system() -> None:
    assert analyze(_repo("makefile_repo")).build_system == BuildSystem.MAKEFILE


def test_makefile_build_files() -> None:
    result = analyze(_repo("makefile_repo"))
    assert any(f.name == "Makefile" for f in result.build_files)


# ninja_repo


def test_ninja_build_system() -> None:
    assert analyze(_repo("ninja_repo")).build_system == BuildSystem.NINJA


def test_ninja_build_files() -> None:
    result = analyze(_repo("ninja_repo"))
    assert any(f.name == "build.ninja" for f in result.build_files)


# unsupported repository


def test_no_signals_raises_unsupported() -> None:
    with pytest.raises(UnsupportedRepositoryError, match="No C/C"):
        analyze(_repo("no_signals_repo"))


# analysis result fields


def test_project_name_preserved() -> None:
    result = analyze(_repo("cmake_repo", project_name="myproject"))
    assert result.project_name == "myproject"


def test_clone_url_preserved() -> None:
    result = analyze(_repo("cmake_repo"))
    assert result.clone_url == _FAKE_URL


def test_repo_ref_preserved() -> None:
    result = analyze(_repo("cmake_repo", repo_ref="v1.2.3"))
    assert result.repo_ref == "v1.2.3"


def test_repo_ref_none_by_default() -> None:
    result = analyze(_repo("cmake_repo"))
    assert result.repo_ref is None


# build system priority


def test_cmake_beats_meson(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text("project(test)\n")
    (tmp_path / "meson.build").write_text("project('test', 'c')\n")
    (tmp_path / "test.h").write_text("#pragma once\n")
    result = analyze(RepoSource(tmp_path, _FAKE_URL, "test", None))
    assert result.build_system == BuildSystem.CMAKE


def test_meson_beats_autotools(tmp_path: Path) -> None:
    (tmp_path / "meson.build").write_text("project('test', 'c')\n")
    (tmp_path / "configure.ac").write_text("AC_INIT([test], [1.0])\n")
    (tmp_path / "test.h").write_text("#pragma once\n")
    result = analyze(RepoSource(tmp_path, _FAKE_URL, "test", None))
    assert result.build_system == BuildSystem.MESON


def test_autotools_beats_makefile(tmp_path: Path) -> None:
    (tmp_path / "configure.ac").write_text("AC_INIT([test], [1.0])\n")
    (tmp_path / "Makefile").write_text("all:\n\t@echo build\n")
    (tmp_path / "test.h").write_text("#pragma once\n")
    result = analyze(RepoSource(tmp_path, _FAKE_URL, "test", None))
    assert result.build_system == BuildSystem.AUTOTOOLS


def test_makefile_beats_ninja(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@echo build\n")
    (tmp_path / "build.ninja").write_text("rule cc\n")
    (tmp_path / "test.h").write_text("#pragma once\n")
    result = analyze(RepoSource(tmp_path, _FAKE_URL, "test", None))
    assert result.build_system == BuildSystem.MAKEFILE


# warnings


def test_warns_when_no_build_system(tmp_path: Path) -> None:
    (tmp_path / "mylib.h").write_text("#pragma once\n")
    result = analyze(RepoSource(tmp_path, _FAKE_URL, "test", None))
    assert result.build_system == BuildSystem.UNKNOWN
    assert any("build system" in w for w in result.warnings)


def test_warns_when_no_headers(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text("project(test)\n")
    result = analyze(RepoSource(tmp_path, _FAKE_URL, "test", None))
    assert any("header" in w for w in result.warnings)


# language detection


def test_language_cpp_from_hpp(tmp_path: Path) -> None:
    (tmp_path / "test.hpp").write_text("#pragma once\n")
    result = analyze(RepoSource(tmp_path, _FAKE_URL, "test", None))
    assert result.language == Language.CPP


def test_language_c_and_cpp(tmp_path: Path) -> None:
    (tmp_path / "test.h").write_text("#pragma once\n")
    (tmp_path / "test.hpp").write_text("#pragma once\n")
    result = analyze(RepoSource(tmp_path, _FAKE_URL, "test", None))
    assert result.language == Language.C_AND_CPP
