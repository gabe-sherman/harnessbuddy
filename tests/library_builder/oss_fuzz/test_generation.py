from __future__ import annotations

from pathlib import Path

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import (
    AutotoolsSetup,
    BuildExplorationResult,
    BuildSystem,
    HarnessExplorationResult,
)
from harnessbuddy.library_builder.oss_fuzz.generation import generate_oss_fuzz

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

_EXPECTED_TOP_LEVEL_FILES = frozenset(
    {
        "project.yaml",
        "Dockerfile",
        "build.sh",
        "build_library.sh",
        "compile_harnesses.sh",
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
    result = generate_oss_fuzz(_analysis(fixture_name), tmp_path / "out")
    for name in _EXPECTED_TOP_LEVEL_FILES:
        assert (result.output_path / name).exists(), f"missing: {name}"
    assert any((result.output_path / "harness_source").glob("default_fuzzer.*"))


def test_generation_result_output_path(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = generate_oss_fuzz(_analysis("cmake_repo"), out)
    assert result.output_path == out


def test_generation_result_project_name(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out")
    assert result.project_name == "mylib"


def test_generation_result_all_files_exist(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out")
    assert all(f.is_file() for f in result.files)


# project.yaml — full content assertions verify language mapping


def test_project_yaml_cmake_language_cpp(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out")
    content = (result.output_path / "project.yaml").read_text()
    expected = (
        f"homepage: {_FAKE_URL}\n"
        "language: c++\n"
        "sanitizers:\n"
        "  - address\n"
        "  - undefined\n"
        f"main_repo: {_FAKE_URL}\n"
    )
    assert content == expected


def test_project_yaml_meson_language_cpp(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("meson_repo"), tmp_path / "out")
    content = (result.output_path / "project.yaml").read_text()
    expected = (
        f"homepage: {_FAKE_URL}\n"
        "language: c++\n"
        "sanitizers:\n"
        "  - address\n"
        "  - undefined\n"
        f"main_repo: {_FAKE_URL}\n"
    )
    assert content == expected


# Dockerfile — full content assertions verify conditional checkout line


def test_dockerfile_no_ref(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out")
    content = (result.output_path / "Dockerfile").read_text()
    expected = (
        "FROM gcr.io/oss-fuzz-base/base-builder\n"
        f"RUN git clone {_FAKE_URL} $SRC/src\n"
        "COPY harness_source $SRC/harness_source\n"
        "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n"
        "WORKDIR $SRC/src\n"
    )
    assert content == expected


def test_dockerfile_with_ref(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("cmake_repo", repo_ref="v1.3.2"), tmp_path / "out")
    content = (result.output_path / "Dockerfile").read_text()
    expected = (
        "FROM gcr.io/oss-fuzz-base/base-builder\n"
        f"RUN git clone {_FAKE_URL} $SRC/src\n"
        "RUN git -C $SRC/src checkout v1.3.2\n"
        "COPY harness_source $SRC/harness_source\n"
        "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n"
        "WORKDIR $SRC/src\n"
    )
    assert content == expected


# build_library.sh — build command includes correct project name substitution


@pytest.mark.parametrize(
    ("fixture_name", "expected_cmd"),
    [
        ("cmake_repo", "cmake -B $SCRIPT_DIR/build"),
        ("meson_repo", "meson setup"),
        ("autotools_repo", "$SCRIPT_DIR/src/configure"),
        ("autotools_configure_repo", "$SCRIPT_DIR/src/configure"),
        ("autotools_autogen_repo", "$SCRIPT_DIR/src/configure"),
        ("makefile_repo", "make -C $SCRIPT_DIR/src"),
    ],
)
def test_build_library_sh_build_command(
    fixture_name: str, expected_cmd: str, tmp_path: Path
) -> None:
    result = generate_oss_fuzz(_analysis(fixture_name), tmp_path / "out")
    content = (result.output_path / "build_library.sh").read_text()
    assert expected_cmd in content


# determinism


def test_build_sh_deterministic(tmp_path: Path) -> None:
    path_a = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "a").output_path
    path_b = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "b").output_path
    assert (path_a / "build.sh").read_text() == (path_b / "build.sh").read_text()


def test_compile_harnesses_sh_deterministic(tmp_path: Path) -> None:
    path_a = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "a").output_path
    path_b = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "b").output_path
    assert (path_a / "compile_harnesses.sh").read_text() == (
        path_b / "compile_harnesses.sh"
    ).read_text()


def _fake_exploration(
    succeeded: bool = True, *, environment: Environment = Environment.OSS_FUZZ
) -> BuildExplorationResult:
    return BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=succeeded,
        command=["cmake", "-B", "/tmp/build"],
        stdout="-- Configuring done",
        stderr="",
        exit_code=0 if succeeded else 1,
        duration_seconds=1.2,
        environment=environment,
    )


# autotools setup detection


def test_autotools_setup_configure(tmp_path: Path) -> None:  # noqa: ARG001
    result = _analysis("autotools_configure_repo")
    assert result.autotools_setup == AutotoolsSetup.CONFIGURE


def test_autotools_setup_autogen(tmp_path: Path) -> None:  # noqa: ARG001
    result = _analysis("autotools_autogen_repo")
    assert result.autotools_setup == AutotoolsSetup.AUTOGEN


def test_autotools_setup_autoreconf(tmp_path: Path) -> None:  # noqa: ARG001
    result = _analysis("autotools_repo")
    assert result.autotools_setup == AutotoolsSetup.AUTORECONF


def test_non_autotools_setup_is_none(tmp_path: Path) -> None:  # noqa: ARG001
    result = _analysis("cmake_repo")
    assert result.autotools_setup is None


# autotools build_library.sh — conditional setup steps


def test_build_library_sh_autotools_configure_no_setup_step(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("autotools_configure_repo"), tmp_path / "out")
    content = (result.output_path / "build_library.sh").read_text()
    assert "autoreconf" not in content
    assert "autogen.sh" not in content


def test_build_library_sh_autotools_autogen_runs_autogen(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("autotools_autogen_repo"), tmp_path / "out")
    content = (result.output_path / "build_library.sh").read_text()
    assert "./autogen.sh" in content


def test_build_library_sh_autotools_autoreconf_runs_autoreconf(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("autotools_repo"), tmp_path / "out")
    content = (result.output_path / "build_library.sh").read_text()
    assert "autoreconf -fiv" in content


# autotools Dockerfile apt deps — conditional on setup type


def test_dockerfile_autotools_configure_no_apt_deps(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("autotools_configure_repo"), tmp_path / "out")
    content = (result.output_path / "Dockerfile").read_text()
    assert "apt-get" not in content


def test_dockerfile_autotools_autogen_has_apt_deps(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("autotools_autogen_repo"), tmp_path / "out")
    content = (result.output_path / "Dockerfile").read_text()
    assert "apt-get install" in content
    assert "autoconf" in content
    assert "automake" in content
    assert "libtool" in content


def test_dockerfile_autotools_autoreconf_has_apt_deps(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("autotools_repo"), tmp_path / "out")
    content = (result.output_path / "Dockerfile").read_text()
    assert "apt-get install" in content
    assert "autoconf" in content


def test_existing_output_dir_raises(tmp_path: Path) -> None:
    output_path = tmp_path / "out"
    output_path.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        generate_oss_fuzz(_analysis("cmake_repo"), output_path)


# build_library.sh — reuse of the explored (possibly agent-fixed) script


def test_build_library_sh_copies_explored_script_verbatim(tmp_path: Path) -> None:
    explored = tmp_path / "explored_build_library.sh"
    explored.write_text("#!/bin/bash\n# agent fix: -DCARES_STATIC=ON\n")
    exploration = _fake_exploration()
    exploration.script_path = explored
    result = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out", exploration)
    content = (result.output_path / "build_library.sh").read_text()
    assert content == explored.read_text()


def test_build_library_sh_falls_back_to_template_without_script_path(tmp_path: Path) -> None:
    result = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out", _fake_exploration())
    content = (result.output_path / "build_library.sh").read_text()
    assert "$SCRIPT_DIR/src" in content


def test_build_library_sh_falls_back_to_template_when_environment_mismatched(
    tmp_path: Path,
) -> None:
    """A local-environment result is never copied verbatim into oss-fuzz output (FR-008) —
    it wasn't validated against the container this project targets."""
    explored = tmp_path / "explored_build_library.sh"
    explored.write_text("#!/bin/bash\n# agent fix: -DCARES_STATIC=ON\n")
    exploration = _fake_exploration(environment=Environment.LOCAL)
    exploration.script_path = explored
    result = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out", exploration)
    content = (result.output_path / "build_library.sh").read_text()
    assert content != explored.read_text()
    assert "$SCRIPT_DIR/src" in content


# build_library.sh — never carries compile-commands capture instrumentation (T011)


@pytest.mark.parametrize("fixture_name", _ALL_BUILD_SYSTEMS)
def test_build_library_sh_has_no_capture_instrumentation_template_fallback(
    fixture_name: str, tmp_path: Path
) -> None:
    """The regenerated template must never carry CMake/bear capture-only flags —
    capture is applied at the orchestration level (explore()), never baked into
    build_library_script()'s output, so the shipped oss-fuzz script is structurally
    unaffected (spec 010 User Story 2)."""
    result = generate_oss_fuzz(_analysis(fixture_name), tmp_path / "out")
    content = (result.output_path / "build_library.sh").read_text()
    assert "CMAKE_EXPORT_COMPILE_COMMANDS" not in content
    assert "bear" not in content


def test_build_library_sh_has_no_capture_instrumentation_copied_verbatim(
    tmp_path: Path,
) -> None:
    """The copy-verbatim path also never leaks capture instrumentation, since
    explore() never writes it into the script text it hands off as script_path."""
    explored = tmp_path / "explored_build_library.sh"
    explored.write_text("#!/bin/bash\nset -euo pipefail\ncmake -B build -S src\n")
    exploration = _fake_exploration()
    exploration.script_path = explored
    result = generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out", exploration)
    content = (result.output_path / "build_library.sh").read_text()
    assert "CMAKE_EXPORT_COMPILE_COMMANDS" not in content
    assert "bear" not in content


# compile_harnesses.sh — reuse of the validated (possibly agent-fixed) script


def _fake_harness(
    script_path: Path | None = None, *, environment: Environment = Environment.OSS_FUZZ
) -> HarnessExplorationResult:
    return HarnessExplorationResult(
        succeeded=True,
        command=["bash", "compile_harnesses.sh"],
        static_libs=[Path("libfoo.a")],
        include_dir=Path("/tmp/install/include"),
        transitive_link_flags=["-lresolv"],
        stdout="",
        stderr="",
        exit_code=0,
        script_path=script_path,
        environment=environment,
    )


def test_compile_harnesses_sh_copies_validated_script_verbatim(tmp_path: Path) -> None:
    validated = tmp_path / "validated_compile_harnesses.sh"
    validated.write_text("#!/bin/bash\n# agent fix: added -lresolv\n")
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", harness_exploration=_fake_harness(validated)
    )
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert content == validated.read_text()


def test_compile_harnesses_sh_falls_back_to_template_when_environment_mismatched(
    tmp_path: Path,
) -> None:
    validated = tmp_path / "validated_compile_harnesses.sh"
    validated.write_text("#!/bin/bash\n# agent fix: added -lresolv\n")
    harness = _fake_harness(validated, environment=Environment.LOCAL)
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", harness_exploration=harness
    )
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert content != validated.read_text()
    assert "libfoo.a" in content
