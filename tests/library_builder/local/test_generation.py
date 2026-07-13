from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.local.generation import generate_local
from harnessbuddy.library_builder.models import (
    BuildExplorationResult,
    BuildSystem,
    HarnessExplorationResult,
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
    result = generate_local(_analysis(fixture_name), tmp_path / "out")
    assert (result.output_path / "setup.sh").exists()
    assert (result.output_path / "build_library.sh").exists()
    assert (result.output_path / "compile_harnesses.sh").exists()
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


# build_library.sh — reuse of the explored (possibly agent-fixed) script


def _exploration_with_script(script_path: Path | None) -> BuildExplorationResult:
    return BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        script_path=script_path,
    )


def test_build_library_sh_copies_explored_script_verbatim(tmp_path: Path) -> None:
    explored = tmp_path / "explored_build_library.sh"
    explored.write_text("#!/bin/bash\n# agent fix: -DCARES_STATIC=ON\n")
    result = generate_local(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_with_script(explored)
    )
    content = (result.output_path / "build_library.sh").read_text()
    assert content == explored.read_text()


def test_build_library_sh_falls_back_to_template_without_script_path(tmp_path: Path) -> None:
    exploration = _exploration_with_script(None)
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out", exploration)
    content = (result.output_path / "build_library.sh").read_text()
    assert "$SCRIPT_DIR/src" in content


def test_build_library_sh_falls_back_to_template_without_exploration(tmp_path: Path) -> None:
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out")
    content = (result.output_path / "build_library.sh").read_text()
    assert "$SCRIPT_DIR/src" in content


# build_library.sh — never carries compile-commands capture instrumentation (T010)


@pytest.mark.parametrize("fixture_name", _ALL_BUILD_SYSTEMS)
def test_build_library_sh_has_no_capture_instrumentation_template_fallback(
    fixture_name: str, tmp_path: Path
) -> None:
    """The regenerated template (no exploration, or exploration without a copyable
    script) must never carry CMake/bear capture-only flags — capture is applied at
    the orchestration level (explore()), never baked into build_library_script()'s
    output, so shipped scripts are structurally unaffected (spec 010 User Story 2)."""
    result = generate_local(_analysis(fixture_name), tmp_path / "out")
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
    result = generate_local(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_with_script(explored)
    )
    content = (result.output_path / "build_library.sh").read_text()
    assert "CMAKE_EXPORT_COMPILE_COMMANDS" not in content
    assert "bear" not in content


# compile_harnesses.sh — reuse of the validated (possibly agent-fixed) script


def _harness_with_script(script_path: Path | None) -> HarnessExplorationResult:
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
    )


def test_compile_harnesses_sh_copies_validated_script_verbatim(tmp_path: Path) -> None:
    validated = tmp_path / "validated_compile_harnesses.sh"
    validated.write_text("#!/bin/bash\n# agent fix: added -lresolv\n")
    result = generate_local(
        _analysis("cmake_repo"),
        tmp_path / "out",
        harness_exploration=_harness_with_script(validated),
    )
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert content == validated.read_text()


def test_compile_harnesses_sh_falls_back_to_template_without_script_path(tmp_path: Path) -> None:
    result = generate_local(
        _analysis("cmake_repo"), tmp_path / "out", harness_exploration=_harness_with_script(None)
    )
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert "libfoo.a" in content
    assert "-lresolv" in content


# workspace copy — build_library.sh/compile_harnesses.sh/harness_src/* copied verbatim
# from the validated workspace instead of re-derived (T017, T020, FR-005)


def _validated_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "build_library.sh").write_text("#!/bin/bash\n# validated build\n")
    (workspace / "compile_harnesses.sh").write_text("#!/bin/bash\n# validated harness\n")
    harness_src = workspace / "harness_src"
    harness_src.mkdir()
    (harness_src / "default_fuzzer.cc").write_text("// discovered CXX is required\n")
    (harness_src / "extra_helper.c").write_text("// other harness_src content\n")
    return workspace


def test_workspace_copy_build_library_sh_byte_identical(tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    exploration = _exploration_with_script(workspace / "build_library.sh")
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out", exploration)
    content = (result.output_path / "build_library.sh").read_text()
    assert content == (workspace / "build_library.sh").read_text()


def test_workspace_copy_compile_harnesses_sh_byte_identical_even_when_unset_on_result(
    tmp_path: Path,
) -> None:
    """compile_harnesses.sh is copied from the workspace whenever it exists there —
    even a harness result with script_path=None (e.g. still a stub) — since the
    workspace is the single source of truth for what was actually validated."""
    workspace = _validated_workspace(tmp_path)
    exploration = _exploration_with_script(workspace / "build_library.sh")
    harness = _harness_with_script(None)
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out", exploration, harness)
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert content == (workspace / "compile_harnesses.sh").read_text()


def test_workspace_copy_harness_src_includes_discovered_default_fuzzer(tmp_path: Path) -> None:
    """The validated workspace's default_fuzzer.cc (discovery having upgraded it from .c to
    .cc on a CXX finding) is copied verbatim, not clobbered by a fresh write_default_fuzzer
    call derived from the (possibly stale) static analysis language."""
    workspace = _validated_workspace(tmp_path)
    exploration = _exploration_with_script(workspace / "build_library.sh")
    result = generate_local(_analysis("cmake_repo"), tmp_path / "out", exploration)
    output_names = {p.name for p in (result.output_path / "harness_src").iterdir()}
    assert "extra_helper.c" in output_names
    assert "default_fuzzer.cc" in output_names
    assert "default_fuzzer.c" not in output_names
    assert (result.output_path / "harness_src" / "extra_helper.c").read_text() == (
        workspace / "harness_src" / "extra_helper.c"
    ).read_text()
