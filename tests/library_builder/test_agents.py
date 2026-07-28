from __future__ import annotations

import json
from dataclasses import replace
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
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import (
    AgentReport,
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
    prompt = build_library_prompt(
        _analysis(tmp_path), exploration, tmp_path / "work", Environment.LOCAL
    )
    assert error_line in prompt
    assert preamble_line not in prompt


# verification command (T027, FR-009)


def test_library_prompt_local_environment_references_check_local_build(tmp_path: Path) -> None:
    exploration = _failed_cmake_exploration(tmp_path)
    prompt = build_library_prompt(
        _analysis(tmp_path), exploration, tmp_path / "work", Environment.LOCAL
    )
    assert "check_local_build.sh" in prompt
    assert str(tmp_path / "work") in prompt
    assert "check_docker_build.sh" not in prompt


def test_library_prompt_oss_fuzz_environment_references_check_docker_build(
    tmp_path: Path,
) -> None:
    exploration = _failed_cmake_exploration(tmp_path)
    prompt = build_library_prompt(
        _analysis(tmp_path),
        exploration,
        tmp_path / "work",
        Environment.OSS_FUZZ,
    )
    assert "check_docker_build.sh" in prompt
    assert str(tmp_path / "work") in prompt
    assert "testlib" in prompt
    assert "check_local_build.sh" not in prompt


# package installation policy (environment-conditioned)


def test_library_prompt_local_environment_forbids_installing_packages(tmp_path: Path) -> None:
    prompt = build_library_prompt(
        _analysis(tmp_path),
        _failed_cmake_exploration(tmp_path),
        tmp_path / "work",
        Environment.LOCAL,
    )
    assert "Do not run apt-get/brew/dnf install yourself" in prompt
    assert "you MAY add packages directly" not in prompt


def test_library_prompt_oss_fuzz_environment_allows_editing_dockerfile(tmp_path: Path) -> None:
    prompt = build_library_prompt(
        _analysis(tmp_path),
        _failed_cmake_exploration(tmp_path),
        tmp_path / "work",
        Environment.OSS_FUZZ,
    )
    assert "you MAY add packages directly" in prompt
    assert "workdir/Dockerfile" in prompt
    assert "Do not run apt-get/brew/dnf install yourself" not in prompt


def test_harness_prompt_local_environment_forbids_installing_packages(tmp_path: Path) -> None:
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        _failed_harness(""),
        tmp_path / "work" / "install",
        tmp_path / "work",
        Environment.LOCAL,
    )
    assert "Do not run apt-get/brew/dnf install yourself" in prompt


def test_harness_prompt_oss_fuzz_environment_allows_editing_dockerfile(tmp_path: Path) -> None:
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        _failed_harness(""),
        tmp_path / "work" / "install",
        tmp_path / "work",
        Environment.OSS_FUZZ,
    )
    assert "you MAY add packages directly" in prompt


def test_harness_prompt_local_environment_references_check_local_build(tmp_path: Path) -> None:
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        _failed_harness(""),
        tmp_path / "work" / "install",
        tmp_path / "work",
        Environment.LOCAL,
    )
    assert "check_local_build.sh" in prompt
    assert "harness_src" in prompt
    assert "harness_source" not in prompt


def test_harness_prompt_oss_fuzz_environment_references_check_docker_build(
    tmp_path: Path,
) -> None:
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        _failed_harness(""),
        tmp_path / "work" / "install",
        tmp_path / "work",
        Environment.OSS_FUZZ,
    )
    assert "check_docker_build.sh" in prompt
    assert str(tmp_path / "work") in prompt
    assert "harness_source" in prompt


def test_action_required_raises_build_failure_error(tmp_path: Path) -> None:
    action_required_text = "ACTION REQUIRED: install libfoo-dev"
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "agent_report.json").write_text(
        json.dumps(
            {
                "summary": "Could not find the missing package.",
                "missing_apt_packages": ["libfoo-dev"],
                "missing_brew_packages": ["foo"],
            }
        )
    )
    with (
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            return_value=AgentStreamResult(
                combined_text=action_required_text,
                exit_code=1,
                duration_seconds=1.0,
                cost_usd=0.02,
            ),
        ),
        pytest.raises(BuildFailureError) as exc_info,
    ):
        invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            workdir,
        )
    assert action_required_text in exc_info.value.output
    assert exc_info.value.summary.duration_seconds == 1.0
    assert exc_info.value.summary.cost_usd == 0.02
    assert exc_info.value.report == AgentReport(
        summary="Could not find the missing package.",
        missing_apt_packages=["libfoo-dev"],
        missing_brew_packages=["foo"],
    )


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
    assert exc_info.value.report is None


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
        Environment.LOCAL,
    )
    assert error_line in prompt
    assert preamble_line not in prompt


def test_harness_prompt_falls_back_to_stdout_when_stderr_empty(tmp_path: Path) -> None:
    """The oss-fuzz docker-streaming Runner merges stderr into stdout and always
    reports an empty .stderr (core/subprocesses.py's run_command_streaming never
    populates it) -- the prompt must still surface the real diagnostic text from
    .stdout in that case, not render an empty code block."""
    harness = HarnessExplorationResult(
        succeeded=False,
        command=[],
        static_libs=[],
        include_dir=Path("/tmp/install/include"),
        transitive_link_flags=[],
        stdout="ld: undefined reference to `foo_init'",
        stderr="",
        exit_code=1,
    )
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        harness,
        tmp_path / "work" / "install",
        tmp_path / "work",
        Environment.OSS_FUZZ,
    )
    assert "undefined reference to `foo_init'" in prompt


def test_harness_action_required_raises_build_failure_error(tmp_path: Path) -> None:
    action_required_text = "ACTION REQUIRED: install libfoo-dev"
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "agent_report.json").write_text(
        json.dumps(
            {
                "summary": "Could not resolve the undefined symbol.",
                "missing_libs": ["foo"],
                "missing_apt_packages": ["libfoo-dev"],
                "missing_brew_packages": ["foo"],
            }
        )
    )
    with (
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            return_value=AgentStreamResult(
                combined_text=action_required_text,
                exit_code=1,
                duration_seconds=1.0,
                cost_usd=0.04,
            ),
        ),
        pytest.raises(BuildFailureError) as exc_info,
    ):
        invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
        )
    assert action_required_text in exc_info.value.output
    assert exc_info.value.summary.duration_seconds == 1.0
    assert exc_info.value.summary.cost_usd == 0.04
    assert exc_info.value.report == AgentReport(
        summary="Could not resolve the undefined symbol.",
        missing_libs=["foo"],
        missing_apt_packages=["libfoo-dev"],
        missing_brew_packages=["foo"],
    )


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
    assert exc_info.value.report is None


def test_harness_agent_unresolved_package_preserves_libs_and_adds_link_flag(
    tmp_path: Path,
) -> None:
    """Regression test for the curl/openldap run: the agent exits 0 having only
    identified an *additional* missing library (not fixed the link), so
    _validate_harness_artifacts still fails it. The pre-agent linker-detected
    libs must survive (not get wiped by re-parsing the unrelated validation-error
    string), the agent's own bare lib name must gain a matching -l flag, and its
    apt/brew names must surface distinctly rather than collapsing to one list.
    """
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "compile_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcurl.a"\n)\n\nEXTRA_LINK_FLAGS="-lssl"\n'
    )
    (workdir / "agent_report.json").write_text(
        json.dumps(
            {
                "summary": "curl also needs LDAP; not installed on this host.",
                "missing_libs": ["ldap"],
                "missing_apt_packages": ["libldap2-dev"],
                "missing_brew_packages": ["openldap"],
            }
        )
    )
    harness = _failed_harness("ld: cannot find -lssl")
    harness = replace(harness, missing_system_libs=["ssl"], transitive_link_flags=["-lssl"])
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(combined_text="done", exit_code=0, duration_seconds=1.0),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            harness,
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
        )
    assert result.succeeded is False
    assert result.missing_system_libs == ["ssl", "ldap"]
    assert result.transitive_link_flags == ["-lssl", "-lldap"]
    assert result.missing_apt_packages == ["libldap2-dev"]
    assert result.missing_brew_packages == ["openldap"]


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
    (workdir / "out" / "default_fuzzer").write_text("stub binary")
    (workdir / "compile_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\nEXTRA_LINK_FLAGS="-lresolv"\n'
    )
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="Editing compile_harnesses.sh", exit_code=0, duration_seconds=3.0
        ),
    ):
        invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `ares_getaddrinfo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
        )
    report = (workdir / "agent_harness_build.log").read_text()
    assert report.startswith("Editing compile_harnesses.sh")
    assert "=== Agent Run Summary ===" in report
    assert "backend:" in report
    assert "outcome: succeeded" in report
    assert "duration:" in report


def test_harness_agent_uses_report_paths_when_script_has_none(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "default_fuzzer").write_text("stub binary")
    (workdir / "compile_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\nEXTRA_LINK_FLAGS="-lresolv"\n'
    )
    (workdir / "agent_report.json").write_text(
        json.dumps(
            {
                "extra_include_paths": ["/usr/include/foo"],
                "extra_library_paths": ["/usr/lib/x86_64-linux-gnu"],
            }
        )
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
    assert result.extra_include_paths == ["/usr/include/foo"]
    assert result.extra_library_paths == ["/usr/lib/x86_64-linux-gnu"]


def test_harness_agent_uses_script_paths_when_report_has_none(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "default_fuzzer").write_text("stub binary")
    (workdir / "compile_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\n'
        'EXTRA_LINK_FLAGS="-lresolv"\nEXTRA_LIB_PATHS="-L/opt/lib"\n'
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
    assert result.extra_library_paths == ["/opt/lib"]


def test_harness_agent_unions_report_and_script_paths(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "default_fuzzer").write_text("stub binary")
    (workdir / "compile_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\n'
        'EXTRA_LINK_FLAGS="-lresolv"\nEXTRA_LIB_PATHS="-L/opt/lib"\n'
    )
    (workdir / "agent_report.json").write_text(
        json.dumps({"extra_library_paths": ["/usr/lib/x86_64-linux-gnu"]})
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
    assert result.extra_library_paths == ["/opt/lib", "/usr/lib/x86_64-linux-gnu"]


# post-agent validation is environment-conditioned: check_docker_build.sh's docker run
# is deliberately unmounted, so install/out never land on the oss-fuzz host workdir.


def test_library_agent_oss_fuzz_success_does_not_require_host_install_dir(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="...\nOK: docker build and in-container compile succeeded\n",
            exit_code=0,
            duration_seconds=1.0,
        ),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            workdir,
            environment=Environment.OSS_FUZZ,
        )
    assert result.succeeded is True


def test_library_agent_oss_fuzz_rejects_false_success_claim(tmp_path: Path) -> None:
    """Defense-in-depth: an agent that exits 0 without ever showing
    check_docker_build.sh's own success marker in its transcript is not trusted."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="I believe this is fixed now.", exit_code=0, duration_seconds=1.0
        ),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            workdir,
            environment=Environment.OSS_FUZZ,
        )
    assert result.succeeded is False


def test_harness_agent_oss_fuzz_success_does_not_require_host_out_dir(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "compile_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\nEXTRA_LINK_FLAGS="-lresolv"\n'
    )
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="...\nOK: docker build and in-container compile succeeded\n",
            exit_code=0,
            duration_seconds=1.0,
        ),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `ares_getaddrinfo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
            environment=Environment.OSS_FUZZ,
        )
    assert result.succeeded is True


def test_harness_agent_oss_fuzz_rejects_false_success_claim(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "compile_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\nEXTRA_LINK_FLAGS="-lresolv"\n'
    )
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="I believe this is fixed now.", exit_code=0, duration_seconds=1.0
        ),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `ares_getaddrinfo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
            environment=Environment.OSS_FUZZ,
        )
    assert result.succeeded is False


def test_harness_agent_success_reparses_fixed_script(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "default_fuzzer").write_text("stub binary")
    (workdir / "compile_harness.sh").write_text(
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
    assert result.script_path == workdir / "compile_harness.sh"


# build_library.sh reuse after a repair — a fix must not be silently discarded


_PORTABLE_REPAIRED_SCRIPT = (
    "#!/bin/bash\nset -euo pipefail\n"
    'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    'BUILD_PREFIX="${BUILD_PREFIX:-$SCRIPT_DIR}"\n'
    'perl "$SCRIPT_DIR/src/Configure" no-shared --prefix="$BUILD_PREFIX/install"\n'
    "make -j\nmake install_dev\n"
)


def _repaired_workdir(tmp_path: Path, script: str | None) -> Path:
    """A workdir that passes the agent's post-run install check, holding `script` if given."""
    workdir = tmp_path / "work"
    (workdir / "install" / "lib").mkdir(parents=True)
    (workdir / "install" / "include").mkdir(parents=True)
    (workdir / "install" / "lib" / "libfoo.a").write_text("stub")
    (workdir / "install" / "include" / "foo.h").write_text("stub")
    if script is not None:
        (workdir / "build_library.sh").write_text(script)
    return workdir


def _repair(tmp_path: Path, workdir: Path, *, exit_code: int = 0) -> BuildExplorationResult:
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="fixed the build", exit_code=exit_code, duration_seconds=1.0
        ),
    ):
        return invoke_library_builder_agent(
            _analysis(tmp_path), _failed_cmake_exploration(tmp_path), workdir
        )


def test_library_agent_publishes_a_portable_repaired_script(tmp_path: Path) -> None:
    """A repair made against a non-standard source layout is still reusable when portable.

    The old layout-only gate dropped it here and let generation regenerate from the template —
    which for an undetected build system is an empty stub, so the published scaffold could not
    build the library at all even though the agent had just built it.
    """
    workdir = _repaired_workdir(tmp_path, _PORTABLE_REPAIRED_SCRIPT)
    result = _repair(tmp_path, workdir)
    assert result.succeeded is True
    assert result.script_path == workdir / "build_library.sh"


def test_library_agent_publishes_a_script_that_falls_back_to_the_session_path(
    tmp_path: Path,
) -> None:
    """Resolving $SCRIPT_DIR/src first and keeping the session checkout as a fallback travels fine.

    This is the shape agents actually produce against a non-standard layout, so *mentioning* the
    session path must not disqualify a script -- only relying on it exclusively does.
    """
    workdir = _repaired_workdir(
        tmp_path,
        f'if [ -d "$SCRIPT_DIR/src" ]; then\n  SOURCE_DIR="$SCRIPT_DIR/src"\n'
        f'else\n  SOURCE_DIR="{tmp_path.resolve()}"\nfi\n'
        f'perl "$SOURCE_DIR/Configure" no-shared\n',
    )
    assert _repair(tmp_path, workdir).script_path == workdir / "build_library.sh"


def test_library_agent_withholds_a_script_knowing_only_the_session_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one thing that really rules out reuse: the session path as the sole source."""
    workdir = _repaired_workdir(
        tmp_path, f'cmake -S "{tmp_path.resolve()}" -B "$BUILD_PREFIX/build"\n'
    )
    result = _repair(tmp_path, workdir)
    assert result.succeeded is True
    assert result.script_path is None
    assert "will not exist wherever this is published" in capsys.readouterr().err


def test_library_agent_withholds_a_script_it_never_wrote(tmp_path: Path) -> None:
    workdir = _repaired_workdir(tmp_path, None)
    assert _repair(tmp_path, workdir).script_path is None


def test_library_agent_withholds_the_script_when_the_repair_failed(tmp_path: Path) -> None:
    """An unvalidated script must not be published: install/ is absent, so the fix is unproven."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "build_library.sh").write_text(_PORTABLE_REPAIRED_SCRIPT)
    result = _repair(tmp_path, workdir)
    assert result.succeeded is False
    assert result.script_path is None


def test_library_agent_keeps_publishing_under_the_standard_layout(tmp_path: Path) -> None:
    """The workdir/src layout is portable by construction and stays unconditionally reusable."""
    workdir = _repaired_workdir(tmp_path, _PORTABLE_REPAIRED_SCRIPT)
    source = workdir / "src"
    source.mkdir()
    analysis = replace(_analysis(tmp_path), source_path=source)
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(combined_text="fixed", exit_code=0, duration_seconds=1.0),
    ):
        result = invoke_library_builder_agent(
            analysis, _failed_cmake_exploration(tmp_path), workdir
        )
    assert result.script_path == workdir / "build_library.sh"
