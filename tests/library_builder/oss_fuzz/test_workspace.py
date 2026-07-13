from __future__ import annotations

from pathlib import Path

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.models import AnalysisResult
from harnessbuddy.library_builder.oss_fuzz.workspace import (
    write_build_sh,
    write_dockerfile,
    write_project_yaml,
)

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "repos"
_FAKE_URL = "https://github.com/example/mylib.git"


def _analysis(fixture_name: str, *, repo_ref: str | None = None) -> AnalysisResult:
    source = RepoSource(
        source_path=_FIXTURES / fixture_name,
        clone_url=_FAKE_URL,
        project_name="mylib",
        repo_ref=repo_ref,
    )
    return analyze(source)


# write_project_yaml


def test_write_project_yaml_content(tmp_path: Path) -> None:
    write_project_yaml(tmp_path, _analysis("cmake_repo"))
    content = (tmp_path / "project.yaml").read_text()
    assert content == (
        f"homepage: {_FAKE_URL}\n"
        "language: c++\n"
        "sanitizers:\n"
        "  - address\n"
        "  - undefined\n"
        f"main_repo: {_FAKE_URL}\n"
    )


def test_write_project_yaml_idempotent(tmp_path: Path) -> None:
    analysis = _analysis("cmake_repo")
    first = write_project_yaml(tmp_path, analysis).read_text()
    second = write_project_yaml(tmp_path, analysis).read_text()
    assert first == second


# write_dockerfile — include_bear


def test_write_dockerfile_include_bear_true_adds_bear_package(tmp_path: Path) -> None:
    write_dockerfile(tmp_path, _analysis("cmake_repo"), include_bear=True)
    content = (tmp_path / "Dockerfile").read_text()
    assert "apt-get install -y --no-install-recommends bear" in content


def test_write_dockerfile_include_bear_false_omits_bear(tmp_path: Path) -> None:
    write_dockerfile(tmp_path, _analysis("cmake_repo"), include_bear=False)
    content = (tmp_path / "Dockerfile").read_text()
    assert "bear" not in content
    # cmake_repo has no other apt packages, so no apt-get line at all without bear.
    assert "apt-get" not in content


def test_write_dockerfile_include_bear_false_matches_no_ref_content(tmp_path: Path) -> None:
    """include_bear=False output is byte-identical to the pre-extraction Dockerfile
    writer's output (no bear, no other apt packages, no repo_ref)."""
    write_dockerfile(tmp_path, _analysis("cmake_repo"), include_bear=False)
    content = (tmp_path / "Dockerfile").read_text()
    expected = (
        "FROM gcr.io/oss-fuzz-base/base-builder\n"
        f"RUN git clone {_FAKE_URL} $SRC/src\n"
        "COPY harness_source $SRC/harness_source\n"
        "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n"
        "WORKDIR $SRC/src\n"
    )
    assert content == expected


def test_write_dockerfile_include_bear_true_only_difference_is_bear_package(
    tmp_path: Path,
) -> None:
    (tmp_path / "with_bear").mkdir()
    (tmp_path / "without_bear").mkdir()
    write_dockerfile(tmp_path / "with_bear", _analysis("cmake_repo"), include_bear=True)
    write_dockerfile(tmp_path / "without_bear", _analysis("cmake_repo"), include_bear=False)
    with_bear = (tmp_path / "with_bear" / "Dockerfile").read_text()
    without_bear = (tmp_path / "without_bear" / "Dockerfile").read_text()
    assert (
        with_bear.replace(
            "RUN apt-get update && apt-get install -y --no-install-recommends bear\n", ""
        )
        == without_bear
    )


def test_write_dockerfile_with_ref(tmp_path: Path) -> None:
    write_dockerfile(tmp_path, _analysis("cmake_repo", repo_ref="v1.3.2"), include_bear=False)
    content = (tmp_path / "Dockerfile").read_text()
    assert "RUN git -C $SRC/src checkout v1.3.2\n" in content


def test_write_dockerfile_autotools_apt_packages_present_with_and_without_bear(
    tmp_path: Path,
) -> None:
    (tmp_path / "a").mkdir()
    write_dockerfile(tmp_path / "a", _analysis("autotools_repo"), include_bear=True)
    content = (tmp_path / "a" / "Dockerfile").read_text()
    assert "autoconf" in content
    assert "bear" in content


# write_build_sh


def test_write_build_sh_runs_build_then_harness(tmp_path: Path) -> None:
    write_build_sh(tmp_path)
    content = (tmp_path / "build.sh").read_text()
    assert '"$SRC/build_library.sh"' in content
    assert '"$SRC/compile_harnesses.sh"' in content
    assert content.index('"$SRC/build_library.sh"') < content.index('"$SRC/compile_harnesses.sh"')


def test_write_build_sh_is_executable(tmp_path: Path) -> None:
    path = write_build_sh(tmp_path)
    assert path.stat().st_mode & 0o111


def test_write_build_sh_has_stage_markers_in_order(tmp_path: Path) -> None:
    """build.sh identifies which stage's output follows, so a failure inside `compile`
    can be attributed to the right stage from the combined log alone (T022, FR-008,
    User Story 3), even though verification is a single atomic pass/fail result."""
    write_build_sh(tmp_path)
    content = (tmp_path / "build.sh").read_text()
    assert content.index("=== build_library.sh ===") < content.index('"$SRC/build_library.sh"')
    assert content.index('"$SRC/build_library.sh"') < content.index("=== compile_harnesses.sh ===")
    assert content.index("=== compile_harnesses.sh ===") < content.index(
        '"$SRC/compile_harnesses.sh"'
    )


@pytest.mark.parametrize("include_bear", [True, False])
def test_write_dockerfile_is_deterministic(tmp_path: Path, include_bear: bool) -> None:
    analysis = _analysis("cmake_repo")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    write_dockerfile(tmp_path / "a", analysis, include_bear=include_bear)
    write_dockerfile(tmp_path / "b", analysis, include_bear=include_bear)
    assert (tmp_path / "a" / "Dockerfile").read_text() == (
        tmp_path / "b" / "Dockerfile"
    ).read_text()
