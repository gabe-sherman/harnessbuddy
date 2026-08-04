"""LocalExecutor: each stage probes on the host, and one shared gate proves the build.

The gate (check_build.sh, the same script a repair agent is told to run) runs once, after the
harness probe succeeds, rebuilding the library from nothing.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.build_parameters import BuildParameters
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.environments.local import LocalExecutor
from harnessbuddy.library_builder.models import AnalysisResult, BuildSystem, Language

_FAKE_URL = "https://github.com/example/testlib.git"

_EXPLORE_OK = RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1)
_PROBE_OK = RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1)
_VERIFY_OK = RunResult(stdout="OK: artifacts present", stderr="", exit_code=0, duration_seconds=0.2)


def _analysis(
    source_path: Path, *, build_system: BuildSystem = BuildSystem.CMAKE
) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=build_system,
        language=Language.C,
        clone_url=_FAKE_URL,
        repo_ref=None,
    )


def _harness_parameters(harness_cflags: str) -> BuildParameters:
    return dataclasses.replace(
        BuildParameters.defaults(),
        harness_cflags=harness_cflags,
        harness_cxxflags=harness_cflags,
    )


@contextlib.contextmanager
def _patched_library_build(explore_result: RunResult = _EXPLORE_OK) -> Iterator[None]:
    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=explore_result,
        ),
        patch(
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
    ):
        yield


@contextlib.contextmanager
def _installed_library(tmp_path: Path) -> Iterator[Path]:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()
    yield install_dir


def test_check_availability_never_raises() -> None:
    LocalExecutor().check_availability()


# run_library_build — the probe decides this stage, and the workspace comes first


def test_run_library_build_tags_environment_local(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    with _patched_library_build():
        result = LocalExecutor().run_library_build(_analysis(source), workdir)
    assert result.environment is Environment.LOCAL
    assert result.succeeded is True


def test_run_library_build_materializes_the_project_before_building(tmp_path: Path) -> None:
    """The gate a repair agent is handed compiles harness_source/, so the scaffold has to exist
    regardless of how the build went."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    with _patched_library_build():
        LocalExecutor().run_library_build(_analysis(source), workdir)
    for name in ("Dockerfile", "project.yaml", "build.sh", "compile_harnesses.sh"):
        assert (workdir / name).is_file(), name
    assert list((workdir / "harness_source").glob("default_fuzzer.*"))


def test_run_library_build_does_not_run_the_gate(tmp_path: Path) -> None:
    """The gate rebuilds the library from nothing, so running it per stage would pay for that
    twice. It runs once, after the harness probe."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    with (
        _patched_library_build(),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming"
        ) as mock_verify,
    ):
        result = LocalExecutor().run_library_build(_analysis(source), workdir)
    mock_verify.assert_not_called()
    assert result.succeeded is True


def test_run_library_build_probe_failure_fails_the_stage(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    failed = RunResult(
        stdout="CMake Error: something went wrong", stderr="", exit_code=1, duration_seconds=0.1
    )
    with _patched_library_build(failed):
        result = LocalExecutor().run_library_build(_analysis(source), workdir)
    assert result.succeeded is False
    assert result.exit_code != 0
    # A repair agent's own verification still needs these to exist.
    assert (workdir / "compile_harnesses.sh").exists()


def test_run_library_build_reports_the_gate_for_an_unknown_build_system(tmp_path: Path) -> None:
    """No build was attempted, so there is no build command to report: the gate is what an
    agent's fix has to satisfy."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    analysis = _analysis(source, build_system=BuildSystem.UNKNOWN)
    result = LocalExecutor().run_library_build(analysis, workdir)
    assert result.succeeded is False
    assert result.command[1].endswith("check_build.sh")
    assert (workdir / "compile_harnesses.sh").exists()


def test_run_library_build_bakes_the_configured_settings_into_the_script(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    parameters = dataclasses.replace(
        BuildParameters.defaults(), cc="gcc", library_cflags="-O2 -DLIBRARY_FLAG"
    )
    with _patched_library_build():
        LocalExecutor().run_library_build(_analysis(source), workdir, parameters=parameters)
    script = (workdir / "build_library.sh").read_text()
    assert 'CC="${CC:-gcc}"' in script
    assert 'CFLAGS="${CFLAGS:--O2 -DLIBRARY_FLAG}"' in script


# run_harness_compile — the probe, then the one gate


def test_run_harness_compile_tags_environment_local(tmp_path: Path) -> None:
    with (
        _installed_library(tmp_path) as install_dir,
        patch(
            "harnessbuddy.library_builder.harness_explorer.run_command",
            return_value=_PROBE_OK,
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=_VERIFY_OK,
        ),
    ):
        result = LocalExecutor().run_harness_compile(install_dir, tmp_path, Language.C)

    assert result.environment is Environment.LOCAL
    assert result.succeeded is True


def test_run_harness_compile_publishes_the_configured_harness_flags(tmp_path: Path) -> None:
    """The published compiler bakes in the caller's harness flags, so it reproduces this run
    when a user invokes it from a bare shell."""
    with (
        _installed_library(tmp_path) as install_dir,
        patch(
            "harnessbuddy.library_builder.harness_explorer.run_command",
            return_value=_PROBE_OK,
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=_VERIFY_OK,
        ),
    ):
        LocalExecutor().run_harness_compile(
            install_dir,
            tmp_path,
            Language.C,
            parameters=_harness_parameters("-fsanitize=fuzzer,address -DHARNESS_FLAG"),
        )

    compiler = (tmp_path / "compile_harness.sh").read_text()
    assert 'CFLAGS="${CFLAGS:--fsanitize=fuzzer,address -DHARNESS_FLAG}"' in compiler


def test_run_harness_compile_gates_the_result_with_check_build_sh(tmp_path: Path) -> None:
    """The stage's pass/fail comes from the gate, not the probe's exit code: the gate is what
    rebuilds from nothing and asserts the artifacts."""
    with (
        _installed_library(tmp_path) as install_dir,
        patch(
            "harnessbuddy.library_builder.harness_explorer.run_command",
            return_value=_PROBE_OK,
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=_VERIFY_OK,
        ) as mock_verify,
    ):
        result = LocalExecutor().run_harness_compile(install_dir, tmp_path, Language.C)

    mock_verify.assert_called_once()
    command = mock_verify.call_args[0][0]
    assert command[1].endswith("check_build.sh")
    assert command[2] == str(tmp_path.resolve())
    assert result.command == command


def test_run_harness_compile_gate_failure_fails_the_stage(tmp_path: Path) -> None:
    with (
        _installed_library(tmp_path) as install_dir,
        patch(
            "harnessbuddy.library_builder.harness_explorer.run_command",
            return_value=_PROBE_OK,
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=RunResult(
                stdout="FAILED: no static libraries",
                stderr="",
                exit_code=1,
                duration_seconds=0.1,
            ),
        ),
    ):
        result = LocalExecutor().run_harness_compile(install_dir, tmp_path, Language.C)

    assert result.succeeded is False
    assert result.exit_code != 0


def test_run_harness_compile_skips_the_gate_without_static_libs(tmp_path: Path) -> None:
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming"
    ) as mock_verify:
        result = LocalExecutor().run_harness_compile(tmp_path / "install", tmp_path, Language.C)
    mock_verify.assert_not_called()
    assert result.succeeded is False


def test_run_harness_compile_skips_the_gate_when_discovery_fails(tmp_path: Path) -> None:
    """Discovery already exhausted its retries against this install/ output, so the gate would
    only reconfirm the same failure at the cost of a full rebuild."""
    with (
        _installed_library(tmp_path) as install_dir,
        patch(
            "harnessbuddy.library_builder.harness_explorer.run_command",
            return_value=RunResult(
                stdout="", stderr="link failed", exit_code=1, duration_seconds=0.1
            ),
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming"
        ) as mock_verify,
    ):
        result = LocalExecutor().run_harness_compile(install_dir, tmp_path, Language.C)

    mock_verify.assert_not_called()
    assert result.succeeded is False
    assert result.command[1].endswith("check_build.sh")
