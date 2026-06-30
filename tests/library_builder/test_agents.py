from __future__ import annotations

from pathlib import Path

import pytest

from harnessbuddy.library_builder.agents import (
    build_library_prompt,
    invoke_library_builder_agent,
)
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    BuildSystem,
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


def test_unknown_tool_raises_valueerror(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown agent tool"):
        invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            tmp_path / "work",
            tool="unknown",
        )
