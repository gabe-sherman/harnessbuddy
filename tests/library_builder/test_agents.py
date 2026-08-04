from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.core.agent_stream import AgentStreamResult
from harnessbuddy.library_builder.agents import (
    build_harness_prompt,
    build_library_prompt,
    invoke_harness_builder_agent,
    invoke_library_builder_agent,
)
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import (
    AgentStopReason,
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


# verification command


def test_library_prompt_local_environment_references_the_gate(tmp_path: Path) -> None:
    exploration = _failed_cmake_exploration(tmp_path)
    prompt = build_library_prompt(
        _analysis(tmp_path), exploration, tmp_path / "work", Environment.LOCAL
    )
    assert "check_build.sh" in prompt
    assert str(tmp_path / "work") in prompt
    assert "check_build_in_container.sh" not in prompt


def test_library_prompt_oss_fuzz_environment_references_the_container_gate(
    tmp_path: Path,
) -> None:
    exploration = _failed_cmake_exploration(tmp_path)
    prompt = build_library_prompt(
        _analysis(tmp_path),
        exploration,
        tmp_path / "work",
        Environment.OSS_FUZZ,
    )
    assert "check_build_in_container.sh" in prompt
    assert str(tmp_path / "work") in prompt
    assert "testlib" in prompt
    assert "check_build.sh" not in prompt


# package installation policy (environment-conditioned)


def test_library_prompt_local_environment_forbids_installing_packages(tmp_path: Path) -> None:
    prompt = build_library_prompt(
        _analysis(tmp_path),
        _failed_cmake_exploration(tmp_path),
        tmp_path / "work",
        Environment.LOCAL,
    )
    assert "Do not run apt-get/dnf install yourself" in prompt
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
    assert "Do not run apt-get/dnf install yourself" not in prompt


def test_harness_prompt_local_environment_forbids_installing_packages(tmp_path: Path) -> None:
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        _failed_harness(""),
        tmp_path / "work" / "install",
        tmp_path / "work",
        Environment.LOCAL,
    )
    assert "Do not run apt-get/dnf install yourself" in prompt


def test_harness_prompt_oss_fuzz_environment_allows_editing_dockerfile(tmp_path: Path) -> None:
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        _failed_harness(""),
        tmp_path / "work" / "install",
        tmp_path / "work",
        Environment.OSS_FUZZ,
    )
    assert "you MAY add packages directly" in prompt


def test_harness_prompt_local_environment_references_the_gate(tmp_path: Path) -> None:
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        _failed_harness(""),
        tmp_path / "work" / "install",
        tmp_path / "work",
        Environment.LOCAL,
    )
    assert "check_build.sh" in prompt
    assert "harness_source" in prompt
    assert "check_build_in_container.sh" not in prompt


def test_harness_prompt_oss_fuzz_environment_references_the_container_gate(
    tmp_path: Path,
) -> None:
    prompt = build_harness_prompt(
        _analysis(tmp_path),
        _failed_harness(""),
        tmp_path / "work" / "install",
        tmp_path / "work",
        Environment.OSS_FUZZ,
    )
    assert "check_build_in_container.sh" in prompt
    assert str(tmp_path / "work") in prompt
    assert "harness_source" in prompt


def test_action_required_is_reported_as_a_failed_result(tmp_path: Path) -> None:
    """Needing a person to act is an expected outcome, not an error, so it comes back as a
    failed result carrying the reason and the agent's own report."""
    action_required_text = "ACTION REQUIRED: install libfoo-dev"
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "agent_report.json").write_text(
        json.dumps(
            {
                "summary": "Could not find the missing package.",
                "missing_apt_packages": ["libfoo-dev"],
            }
        )
    )
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text=action_required_text,
            exit_code=1,
            duration_seconds=1.0,
            cost_usd=0.02,
            model_text=action_required_text,
        ),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            workdir,
        )
    assert result.succeeded is False
    assert result.agent_stop_reason is AgentStopReason.ACTION_REQUIRED
    assert action_required_text in result.stdout
    assert result.duration_seconds == 1.0
    assert result.cost_usd == 0.02
    assert result.agent_summary == "Could not find the missing package."
    assert result.missing_apt_packages == ["libfoo-dev"]
    assert result.script_path is None


def test_budget_limit_is_reported_as_a_failed_result(tmp_path: Path) -> None:
    budget_text = "reached the 5 hour limit"
    (tmp_path / "work").mkdir()
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text=budget_text,
            exit_code=1,
            duration_seconds=2.5,
            cost_usd=0.03,
        ),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            tmp_path / "work",
        )
    assert result.succeeded is False
    assert result.agent_stop_reason is AgentStopReason.BUDGET_LIMITED
    assert result.duration_seconds == 2.5
    assert result.cost_usd == 0.03
    assert result.agent_summary is None


def test_a_failed_library_repair_records_why_compile_commands_are_absent(tmp_path: Path) -> None:
    """compile_commands_path and compile_commands_error are mutually exclusive; without the
    error set, the printed line reads "not captured (None)"."""
    (tmp_path / "work").mkdir()
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(combined_text="gave up", exit_code=1, duration_seconds=1.0),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            tmp_path / "work",
        )
    assert result.compile_commands_path is None
    assert result.compile_commands_error is not None


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
    """The docker-streaming Runner merges stderr into stdout and leaves .stderr empty, so the
    prompt has to surface the diagnostic from .stdout rather than render an empty code
    block."""
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


def test_harness_action_required_is_reported_as_a_failed_result(tmp_path: Path) -> None:
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
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text=action_required_text,
            exit_code=1,
            duration_seconds=1.0,
            cost_usd=0.04,
            model_text=action_required_text,
        ),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
        )
    assert result.succeeded is False
    assert result.agent_stop_reason is AgentStopReason.ACTION_REQUIRED
    assert action_required_text in result.stdout
    assert result.duration_seconds == 1.0
    assert result.cost_usd == 0.04
    assert result.agent_summary == "Could not resolve the undefined symbol."
    assert result.missing_apt_packages == ["libfoo-dev"]
    assert result.script_path is None


def test_harness_budget_limit_is_reported_as_a_failed_result(tmp_path: Path) -> None:
    budget_text = "reached the 5 hour limit"
    (tmp_path / "work").mkdir()
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text=budget_text,
            exit_code=1,
            duration_seconds=3.5,
            cost_usd=0.05,
        ),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=tmp_path / "work" / "install", workdir=tmp_path / "work"),
        )
    assert result.succeeded is False
    assert result.agent_stop_reason is AgentStopReason.BUDGET_LIMITED
    assert result.duration_seconds == 3.5
    assert result.cost_usd == 0.05


def test_harness_agent_unresolved_package_preserves_libs_and_adds_link_flag(
    tmp_path: Path,
) -> None:
    """The agent exits 0 having only identified one more missing library rather than fixing the
    link, so the artifact check still fails it. The pre-agent linker-detected libs must survive,
    the agent's bare lib name must gain a matching -l flag, and its apt package must surface.
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


# post-agent validation checks real artifacts: the gate mounts the workspace, so install/ and
# out/ land on the host either way and nothing has to be inferred from the transcript.


def test_library_agent_success_requires_real_install_artifacts(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="I fixed it, honestly", exit_code=0, duration_seconds=1.0
        ),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            workdir,
            environment=Environment.OSS_FUZZ,
        )
    assert result.succeeded is False
    assert "no static libraries" in result.stderr


def test_library_agent_success_accepted_when_artifacts_are_there(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "install" / "lib").mkdir(parents=True)
    (workdir / "install" / "lib" / "libfoo.a").write_text("archive")
    (workdir / "install" / "include").mkdir()
    (workdir / "install" / "include" / "foo.h").write_text("#pragma once\n")
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(combined_text="fixed", exit_code=0, duration_seconds=1.0),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path),
            _failed_cmake_exploration(tmp_path),
            workdir,
            environment=Environment.OSS_FUZZ,
        )
    assert result.succeeded is True
    assert result.script_path == workdir / "build_library.sh"


def test_harness_agent_success_requires_a_compiled_binary(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="I linked it, honestly", exit_code=0, duration_seconds=1.0
        ),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
            environment=Environment.OSS_FUZZ,
        )
    assert result.succeeded is False
    assert "no compiled harness binary" in result.stderr


def test_harness_agent_success_accepted_in_oss_fuzz_when_the_binary_is_there(
    tmp_path: Path,
) -> None:
    """Regression test for the mbedtls run: the agent fixed the link line and the container
    gate passed, but the harness binary was written to the image's own $OUT=/out — outside the
    /src mount — so nothing reached the host and the repair was rejected. The gate now mounts
    out/, so a verified fix is accepted here exactly as it is locally."""
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "default_fuzzer").write_text("stub binary")
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="dropped the duplicate archive", exit_code=0, duration_seconds=1.0
        ),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("multiple definition of `mbedtls_cipher_supported'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
            environment=Environment.OSS_FUZZ,
        )
    assert result.succeeded is True
    assert result.validation_errors == []
    assert result.script_path == workdir / "compile_harness.sh"


# a rejected repair records why, so the diagnostic can say so instead of quoting the agent


def test_rejected_harness_repair_records_its_validation_errors(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "agent_report.json").write_text(json.dumps({"summary": "Not a missing dep."}))
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(combined_text="fixed", exit_code=0, duration_seconds=1.0),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
        )
    assert result.succeeded is False
    assert len(result.validation_errors) == 1
    assert "no compiled harness binary" in result.validation_errors[0]
    # The agent's own account survives alongside it, as context rather than as the reason.
    assert result.agent_summary == "Not a missing dep."


def test_rejected_library_repair_records_its_validation_errors(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(combined_text="fixed", exit_code=0, duration_seconds=1.0),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path), _failed_cmake_exploration(tmp_path), workdir
        )
    assert result.succeeded is False
    assert any("no static libraries" in error for error in result.validation_errors)


def test_a_repair_that_failed_outright_records_no_validation_errors(tmp_path: Path) -> None:
    """The check only runs on a claimed success, so a non-zero exit leaves the list empty —
    which is what lets the diagnostic tell the two cases apart."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(combined_text="gave up", exit_code=1, duration_seconds=1.0),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path), _failed_cmake_exploration(tmp_path), workdir
        )
    assert result.succeeded is False
    assert result.validation_errors == []


# build_library.sh reuse after a repair — a fix must not be silently discarded


def _repair_result(tmp_path: Path, workdir: Path, *, exit_code: int = 0):  # type: ignore[no-untyped-def]
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text="repaired", exit_code=exit_code, duration_seconds=1.0
        ),
    ):
        return invoke_library_builder_agent(
            _analysis(tmp_path), _failed_cmake_exploration(tmp_path), workdir
        )


def _repaired_workdir(tmp_path: Path) -> Path:
    workdir = tmp_path / "work"
    (workdir / "install" / "lib").mkdir(parents=True)
    (workdir / "install" / "lib" / "libfoo.a").write_text("archive")
    (workdir / "install" / "include").mkdir()
    (workdir / "install" / "include" / "foo.h").write_text("#pragma once\n")
    (workdir / "build_library.sh").write_text("#!/bin/bash\n# repaired\n")
    return workdir


def test_library_agent_publishes_the_repaired_script(tmp_path: Path) -> None:
    """The agent edits build_library.sh in place and the gate ran that script from nothing, so
    the script that passed is the script that ships."""
    workdir = _repaired_workdir(tmp_path)
    result = _repair_result(tmp_path, workdir)
    assert result.succeeded is True
    assert result.script_path == workdir / "build_library.sh"


def test_library_agent_withholds_the_script_when_the_repair_failed(tmp_path: Path) -> None:
    workdir = _repaired_workdir(tmp_path)
    result = _repair_result(tmp_path, workdir, exit_code=1)
    assert result.succeeded is False
    assert result.script_path is None


# ACTION REQUIRED is read from the model's own text, not from its whole transcript


_SKILL_QUOTE_IN_TRANSCRIPT = (
    "Reading /repo/agents/library_builder/SKILL.md\n"
    "68\t   - Say clearly, in your own reply text, what is needed:\n"
    '69\t     "ACTION REQUIRED: Missing system packages detected. Please review '
    "agent_report.json\n"
    '70\t      and install the listed packages, then re-run this agent."\n'
)


def test_marker_quoted_in_tool_output_does_not_fail_a_verified_build(tmp_path: Path) -> None:
    """Reading a file that documents the marker is not a stop signal.

    An agent that repaired the build, verified clean, and left artifacts on disk had also read
    the skill quoting `ACTION REQUIRED` four times — and a transcript-wide substring match
    failed the run over it.
    """
    workdir = _repaired_workdir(tmp_path)
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text=f"{_SKILL_QUOTE_IN_TRANSCRIPT}\nVERIFY_EXIT=0",
            exit_code=0,
            duration_seconds=1.0,
            model_text="Build fixed -- verification exits 0.",
        ),
    ):
        result = invoke_library_builder_agent(
            _analysis(tmp_path), _failed_cmake_exploration(tmp_path), workdir
        )
    assert result.succeeded is True
    assert "outcome: succeeded" in (workdir / "agent_library_build.log").read_text()


def test_marker_quoted_in_tool_output_does_not_fail_a_verified_harness(tmp_path: Path) -> None:
    """Same channel discipline on the harness-builder side, which shares the detection code."""
    workdir = tmp_path / "work"
    (workdir / "out").mkdir(parents=True)
    (workdir / "out" / "default_fuzzer").write_text("stub binary")
    (workdir / "compile_harness.sh").write_text(
        'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libcares.a"\n)\n\nEXTRA_LINK_FLAGS="-lresolv"\n'
    )
    with patch(
        "harnessbuddy.library_builder.agents.run_agent_streaming",
        return_value=AgentStreamResult(
            combined_text=_SKILL_QUOTE_IN_TRANSCRIPT,
            exit_code=0,
            duration_seconds=1.0,
            model_text="Linked successfully.",
        ),
    ):
        result = invoke_harness_builder_agent(
            _analysis(tmp_path),
            _failed_harness("undefined reference to `foo'"),
            HarnessPaths(install_dir=workdir / "install", workdir=workdir),
        )
    assert result.succeeded is True
