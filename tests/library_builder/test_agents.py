from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.core.agent_stream import AgentStreamResult
from harnessbuddy.library_builder.agents import (
    BuildFailureError,
    LLMBudgetError,
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
    (tmp_path / "work").mkdir()
    with (
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            return_value=AgentStreamResult(
                combined_text=action_required_text,
                exit_code=1,
                duration_seconds=1.0,
                cost_usd=0.02,
                final_message="Could not find the missing package.",
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
    assert exc_info.value.summary.duration_seconds == 1.0
    assert exc_info.value.summary.cost_usd == 0.02
    assert exc_info.value.summary.final_message == "Could not find the missing package."


def test_budget_limited_raises_llm_budget_error(tmp_path: Path) -> None:
    budget_text = "reached the 5 hour limit"
    (tmp_path / "work").mkdir()
    with (
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            return_value=AgentStreamResult(
                combined_text=budget_text,
                exit_code=1,
                duration_seconds=2.5,
                cost_usd=0.03,
                final_message="Ran out of usage before finishing.",
            ),
        ),
        pytest.raises(LLMBudgetError) as exc_info,
    ):
        invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            tmp_path / "work",
        )
    assert exc_info.value.summary.duration_seconds == 2.5
    assert exc_info.value.summary.cost_usd == 0.03
    assert exc_info.value.summary.final_message == "Ran out of usage before finishing."


def test_library_agent_populates_agent_summary_on_success(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "install" / "lib").mkdir(parents=True)
    (workdir / "install" / "include").mkdir(parents=True)
    (workdir / "install" / "lib" / "libfoo.a").write_text("stub")
    (workdir / "install" / "include" / "foo.h").write_text("stub")
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="done", exit_code=0, duration_seconds=1.0, final_message="Fixed it."
        ),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            workdir,
        )
    assert result.agent_summary == "Fixed it."


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
    (tmp_path / "work").mkdir()
    with (
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            return_value=AgentStreamResult(
                combined_text=action_required_text,
                exit_code=1,
                duration_seconds=1.0,
                cost_usd=0.04,
                final_message="Could not resolve the undefined symbol.",
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
    assert exc_info.value.summary.duration_seconds == 1.0
    assert exc_info.value.summary.cost_usd == 0.04
    assert exc_info.value.summary.final_message == "Could not resolve the undefined symbol."


def test_harness_budget_limited_raises_llm_budget_error(tmp_path: Path) -> None:
    budget_text = "reached the 5 hour limit"
    (tmp_path / "work").mkdir()
    with (
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            return_value=AgentStreamResult(
                combined_text=budget_text,
                exit_code=1,
                duration_seconds=3.5,
                cost_usd=0.05,
                final_message="Ran out of usage before finishing.",
            ),
        ),
        pytest.raises(LLMBudgetError) as exc_info,
    ):
        invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=tmp_path / "work" / "install", workdir=tmp_path / "work"),
        )
    assert exc_info.value.summary.duration_seconds == 3.5
    assert exc_info.value.summary.cost_usd == 0.05
    assert exc_info.value.summary.final_message == "Ran out of usage before finishing."


def test_harness_agent_populates_agent_summary_on_success(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "probe_harness").write_text("stub binary")
    (workdir / "build_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\nEXTRA_LINK_FLAGS="-lresolv"\n'
    )
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="done", exit_code=0, duration_seconds=1.0, final_message="Fixed it."
        ),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `ares_getaddrinfo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
        )
    assert result.agent_summary == "Fixed it."


def test_harness_unknown_tool_raises_valueerror(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown agent tool"):
        invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=tmp_path / "work" / "install", workdir=tmp_path / "work"),
            tool="unknown",
        )


def test_library_agent_writes_report_file(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "install" / "lib").mkdir(parents=True)
    (workdir / "install" / "include").mkdir(parents=True)
    (workdir / "install" / "lib" / "libfoo.a").write_text("stub")
    (workdir / "install" / "include" / "foo.h").write_text("stub")
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="Reading build_library.sh", exit_code=0, duration_seconds=2.5
        ),
    ):
        invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            workdir,
        )
    report = (workdir / "agent_library_build.log").read_text()
    assert report.startswith("Reading build_library.sh")
    assert "=== Agent Run Summary ===" in report
    assert "backend:" in report
    assert "outcome: succeeded" in report
    assert "duration:" in report


def test_harness_agent_writes_report_file(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "probe_harness").write_text("stub binary")
    (workdir / "build_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\nEXTRA_LINK_FLAGS="-lresolv"\n'
    )
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="Editing build_harness.sh", exit_code=0, duration_seconds=3.0
        ),
    ):
        invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `ares_getaddrinfo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
        )
    report = (workdir / "agent_harness_build.log").read_text()
    assert report.startswith("Editing build_harness.sh")
    assert "=== Agent Run Summary ===" in report
    assert "backend:" in report
    assert "outcome: succeeded" in report
    assert "duration:" in report


def test_harness_agent_success_reparses_fixed_script(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "probe_harness").write_text("stub binary")
    (workdir / "build_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\nEXTRA_LINK_FLAGS="-lresolv"\n'
    )
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(combined_text="done", exit_code=0, duration_seconds=1.0),
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
