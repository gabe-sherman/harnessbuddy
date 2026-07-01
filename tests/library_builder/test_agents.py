from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.agents import (
    BuildFailureError,
    build_harness_prompt,
    build_library_prompt,
    invoke_harness_builder_agent,
    invoke_library_builder_agent,
)
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    BuildSystem,
    HarnessExplorationResult,
    HarnessPaths,
    Language,
)

_FAKE_URL = "https://github.com/example/testlib.git"


def _analysis(tmp_path: Path) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=tmp_path,
        build_system=BuildSystem.CMAKE,
        build_files=[tmp_path / "CMakeLists.txt"],
        headers=[tmp_path / "include" / "foo.h"],
        language=Language.C,
        clone_url=_FAKE_URL,
        repo_ref=None,
    )


def _failed_cmake_exploration(source_path: Path) -> BuildExplorationResult:
    return BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=False,
        command=["bash", str(source_path / "build_library.sh")],
        stdout=(
            "-- The C compiler identification is GNU\n"
            "CMake Error at CMakeLists.txt:5:\n"
            "  Could not find a package configuration file provided by\n"
            '  "NonExistentPackage_abc123" (missing: NonExistentPackage_abc123_DIR)\n'
            "-- Configuring incomplete, errors occurred!\n"
        ),
        stderr="",
        exit_code=1,
        duration_seconds=5.0,
    )


def test_prompt_tail_not_head_when_truncated(tmp_path: Path) -> None:
    preamble_line = "PREAMBLE_LINE_THAT_MUST_NOT_APPEAR"
    error_line = "UNIQUE_ERROR_LINE_THAT_MUST_APPEAR"
    long_stdout = "\n".join([preamble_line] + ["filler"] * 400 + [error_line])
    exploration = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=False,
        command=["bash", str(tmp_path / "build_library.sh")],
        stdout=long_stdout,
        stderr="",
        exit_code=1,
        duration_seconds=1.0,
    )
    prompt = build_library_prompt(_analysis(tmp_path), exploration, tmp_path / "work")
    assert error_line in prompt
    assert preamble_line not in prompt


def test_action_required_raises_build_failure_error(tmp_path: Path) -> None:
    action_required_text = "ACTION REQUIRED: install libfoo-dev"
    with (
        patch(
            "harnessbuddy.library_builder.agents.run_command_streaming",
            return_value=RunResult(
                stdout=action_required_text, stderr="", exit_code=1, duration_seconds=1.0
            ),
        ),
        pytest.raises(BuildFailureError) as exc_info,
    ):
        invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            tmp_path / "work",
        )
    assert action_required_text in exc_info.value.output


def test_unknown_tool_raises_valueerror(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown agent tool"):
        invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            tmp_path / "work",
            tool="unknown",
        )


def _failed_harness(stderr: str) -> HarnessExplorationResult:
    return HarnessExplorationResult(
        succeeded=False,
        command=[],
        static_libs=[],
        include_dir=Path("/tmp/install/include"),
        transitive_link_flags=[],
        stdout="",
        stderr=stderr,
        exit_code=1,
    )


def test_harness_prompt_tail_not_head_when_truncated(tmp_path: Path) -> None:
    preamble_line = "PREAMBLE_LINE_THAT_MUST_NOT_APPEAR"
    error_line = "UNIQUE_ERROR_LINE_THAT_MUST_APPEAR"
    long_stderr = "\n".join([preamble_line] + ["filler"] * 400 + [error_line])
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        _failed_harness(long_stderr),
        tmp_path / "work" / "install",
        tmp_path / "work",
    )
    assert error_line in prompt
    assert preamble_line not in prompt


def test_harness_action_required_raises_build_failure_error(tmp_path: Path) -> None:
    action_required_text = "ACTION REQUIRED: install libfoo-dev"
    with (
        patch(
            "harnessbuddy.library_builder.agents.run_command_streaming",
            return_value=RunResult(
                stdout=action_required_text, stderr="", exit_code=1, duration_seconds=1.0
            ),
        ),
        pytest.raises(BuildFailureError) as exc_info,
    ):
        invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=tmp_path / "work" / "install", workdir=tmp_path / "work"),
        )
    assert action_required_text in exc_info.value.output


def test_harness_unknown_tool_raises_valueerror(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown agent tool"):
        invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=tmp_path / "work" / "install", workdir=tmp_path / "work"),
            tool="unknown",
        )


def test_harness_agent_success_reparses_fixed_script(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "probe_harness").write_text("stub binary")
    (workdir / "build_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\nEXTRA_LINK_FLAGS="-lresolv"\n'
    )
    with patch(
        "harnessbuddy.library_builder.agents.run_command_streaming",
        return_value=RunResult(stdout="done", stderr="", exit_code=0, duration_seconds=1.0),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `ares_getaddrinfo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
        )
    assert result.succeeded is True
    assert result.transitive_link_flags == ["-lresolv"]
    assert result.static_libs == [Path("libcares.a")]
    assert result.script_path == workdir / "build_harness.sh"
