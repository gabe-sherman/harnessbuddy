from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.environments.local import LocalExecutor
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildSystem,
    Language,
)

_FAKE_URL = "https://github.com/example/testlib.git"


def _analysis(source_path: Path) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=BuildSystem.CMAKE,
        build_files=[],
        headers=[],
        language=Language.C,
        clone_url=_FAKE_URL,
        repo_ref=None,
    )


def test_check_availability_never_raises() -> None:
    LocalExecutor().check_availability()


def test_run_library_build_tags_environment_local(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
    ):
        result = LocalExecutor().run_library_build(_analysis(source), workdir)
    assert result.environment is Environment.LOCAL
    assert result.succeeded is True


def test_run_library_build_matches_explore_directly(tmp_path: Path) -> None:
    """LocalExecutor delegates to exploration.explore with identical behavior (T009)."""
    from harnessbuddy.library_builder.exploration import explore

    workdir_a = tmp_path / "a"
    workdir_b = tmp_path / "b"
    source_a = workdir_a / "src"
    source_b = workdir_b / "src"
    source_a.mkdir(parents=True)
    source_b.mkdir(parents=True)

    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
    ):
        via_executor = LocalExecutor().run_library_build(_analysis(source_a), workdir_a)
        via_direct_call = explore(_analysis(source_b), workdir_b)

    assert via_executor.succeeded == via_direct_call.succeeded
    assert via_executor.environment == via_direct_call.environment == Environment.LOCAL
    assert (workdir_a / "build_library.sh").read_text() == (
        workdir_b / "build_library.sh"
    ).read_text()


def test_run_harness_compile_tags_environment_local(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ):
        result = LocalExecutor().run_harness_compile(install_dir, tmp_path, Language.C)

    assert result.environment is Environment.LOCAL
    assert result.succeeded is True
