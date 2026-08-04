from __future__ import annotations

from pathlib import Path

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.build_parameters import BuildParameters
from harnessbuddy.library_builder.models import AnalysisResult
from harnessbuddy.library_builder.workspace import (
    DEFAULT_BASE_IMAGE,
    inject_apt_packages,
    materialize,
    write_build_sh,
    write_dockerfile,
    write_project_yaml,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "repos"
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
    """The exact shipped Dockerfile for a plain project: no bear, no other apt packages, no
    repo_ref checkout."""
    analysis = _analysis("cmake_repo")
    write_dockerfile(tmp_path, analysis, include_bear=False)
    content = (tmp_path / "Dockerfile").read_text()
    expected = (
        "FROM gcr.io/oss-fuzz-base/base-builder:ubuntu-24-04\n"
        f"ENV FUZZING_LANGUAGE={analysis.language.value}\n"
        f"RUN git clone --recursive {_FAKE_URL} $SRC/src\n"
        "COPY harness_source $SRC/harness_source\n"
        "COPY build.sh build_library.sh compile_harness.sh compile_harnesses.sh $SRC/\n"
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


@pytest.mark.parametrize(
    "fixture", ["autotools_repo", "autotools_autogen_repo", "autotools_bootstrap_repo"]
)
def test_write_dockerfile_autotools_apt_packages_present_with_and_without_bear(
    tmp_path: Path, fixture: str
) -> None:
    """Every setup variant that generates configure in-image needs the autotools
    toolchain installed; only a pre-generated configure can go without."""
    (tmp_path / "a").mkdir()
    write_dockerfile(tmp_path / "a", _analysis(fixture), include_bear=True)
    content = (tmp_path / "a" / "Dockerfile").read_text()
    assert "autoconf" in content
    assert "bear" in content


# write_build_sh


def test_write_build_sh_runs_build_then_harness(tmp_path: Path) -> None:
    write_build_sh(tmp_path)
    content = (tmp_path / "build.sh").read_text()
    assert '"$SCRIPT_DIR/build_library.sh"' in content
    assert '"$SCRIPT_DIR/compile_harnesses.sh"' in content
    assert content.index('"$SCRIPT_DIR/build_library.sh"') < content.index(
        '"$SCRIPT_DIR/compile_harnesses.sh"'
    )


def test_write_build_sh_resolves_its_own_directory_rather_than_srcs(tmp_path: Path) -> None:
    """build.sh runs in three places: under `compile`, under the build gate, and by a user in
    the generated output directory. $SCRIPT_DIR is the one reference that resolves in all
    three."""
    write_build_sh(tmp_path)
    assert "$SRC" not in (tmp_path / "build.sh").read_text()


def test_write_build_sh_is_executable(tmp_path: Path) -> None:
    path = write_build_sh(tmp_path)
    assert path.stat().st_mode & 0o111


def test_write_build_sh_has_stage_markers_in_order(tmp_path: Path) -> None:
    """build.sh marks which stage's output follows, so a failure inside `compile` can be
    attributed to the right stage from the combined log, even though the gate reports one
    atomic pass/fail."""
    write_build_sh(tmp_path)
    content = (tmp_path / "build.sh").read_text()
    assert content.index("=== build_library.sh ===") < content.index(
        '"$SCRIPT_DIR/build_library.sh"'
    )
    assert content.index('"$SCRIPT_DIR/build_library.sh"') < content.index(
        "=== compile_harnesses.sh ==="
    )
    assert content.index("=== compile_harnesses.sh ===") < content.index(
        '"$SCRIPT_DIR/compile_harnesses.sh"'
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


# inject_apt_packages — merges newly-discovered packages into an already-written Dockerfile,
# since that file is written early, before the harness phase knows what else is needed.


def test_inject_apt_packages_appends_to_existing_install_line(tmp_path: Path) -> None:
    write_dockerfile(tmp_path, _analysis("cmake_repo"), include_bear=True)
    inject_apt_packages(tmp_path, ["libzstd-dev"])
    content = (tmp_path / "Dockerfile").read_text()
    assert "bear libzstd-dev" in content


def test_inject_apt_packages_dedupes_against_existing_packages(tmp_path: Path) -> None:
    """The build phase may report a package the harness phase later reports again under the
    same apt name, and the merge must not duplicate it."""
    write_dockerfile(
        tmp_path, _analysis("cmake_repo"), include_bear=True, system_packages=["libzstd-dev"]
    )
    inject_apt_packages(tmp_path, ["libzstd-dev"])
    content = (tmp_path / "Dockerfile").read_text()
    assert content.count("libzstd-dev") == 1


def test_inject_apt_packages_preserves_content_around_the_install_line(tmp_path: Path) -> None:
    write_dockerfile(tmp_path, _analysis("cmake_repo"), include_bear=True)
    before = (tmp_path / "Dockerfile").read_text()
    inject_apt_packages(tmp_path, ["libzstd-dev"])
    after = (tmp_path / "Dockerfile").read_text()
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines)
    for before_line, after_line in zip(before_lines, after_lines, strict=True):
        if before_line.startswith("RUN apt-get"):
            continue
        assert before_line == after_line


def test_inject_apt_packages_is_a_noop_without_new_packages(tmp_path: Path) -> None:
    write_dockerfile(tmp_path, _analysis("cmake_repo"), include_bear=True)
    before = (tmp_path / "Dockerfile").read_text()
    inject_apt_packages(tmp_path, [])
    assert (tmp_path / "Dockerfile").read_text() == before


def test_inject_apt_packages_inserts_a_line_when_none_exists(tmp_path: Path) -> None:
    """A Dockerfile with no apt-get install line still needs one added, right after
    ENV FUZZING_LANGUAGE."""
    write_dockerfile(tmp_path, _analysis("cmake_repo"), include_bear=False)
    assert "RUN apt-get" not in (tmp_path / "Dockerfile").read_text()
    inject_apt_packages(tmp_path, ["libzstd-dev"])
    content = (tmp_path / "Dockerfile").read_text()
    lines = content.splitlines()
    apt_index = next(i for i, line in enumerate(lines) if line.startswith("RUN apt-get"))
    env_index = next(i for i, line in enumerate(lines) if line.startswith("ENV FUZZING_LANGUAGE"))
    assert apt_index == env_index + 1
    assert "libzstd-dev" in lines[apt_index]


# --base-image


def test_write_dockerfile_uses_the_default_base_image(tmp_path: Path) -> None:
    write_dockerfile(tmp_path, _analysis("cmake_repo"), include_bear=False)
    assert (tmp_path / "Dockerfile").read_text().startswith(f"FROM {DEFAULT_BASE_IMAGE}\n")


def test_write_dockerfile_honours_an_explicit_base_image(tmp_path: Path) -> None:
    write_dockerfile(
        tmp_path, _analysis("cmake_repo"), include_bear=False, base_image="example.com/base:v1"
    )
    assert (tmp_path / "Dockerfile").read_text().startswith("FROM example.com/base:v1\n")


# materialize — one layout, written before any build is attempted


def test_materialize_writes_the_whole_project_layout(tmp_path: Path) -> None:
    materialize(tmp_path, _analysis("cmake_repo"), parameters=BuildParameters.defaults())
    for name in (
        "project.yaml",
        "Dockerfile",
        "build.sh",
        "compile_harness.sh",
        "compile_harnesses.sh",
    ):
        assert (tmp_path / name).is_file(), name
    assert list((tmp_path / "harness_source").glob("default_fuzzer.*"))


def test_materialize_gives_an_unknown_build_system_a_runnable_gate(tmp_path: Path) -> None:
    """The gate a repair agent is told to run compiles harness_source/, so that scaffold has to
    exist even when no build system was identified and no build was ever attempted."""
    materialize(tmp_path, _analysis("headers_only_repo"), parameters=BuildParameters.defaults())
    assert (tmp_path / "compile_harnesses.sh").is_file()
    assert list((tmp_path / "harness_source").glob("default_fuzzer.*"))


def test_materialize_is_idempotent(tmp_path: Path) -> None:
    analysis = _analysis("cmake_repo")
    materialize(tmp_path, analysis, parameters=BuildParameters.defaults())
    first = {p.name: p.read_text() for p in tmp_path.rglob("*") if p.is_file()}
    materialize(tmp_path, analysis, parameters=BuildParameters.defaults())
    second = {p.name: p.read_text() for p in tmp_path.rglob("*") if p.is_file()}
    assert first == second
