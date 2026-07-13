from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.base import (
    Environment,
    EnvironmentUnavailableError,
)
from harnessbuddy.library_builder.environments.oss_fuzz import OssFuzzExecutor
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildSystem,
    Language,
)

_FAKE_URL = "https://github.com/example/testlib.git"

_VERIFY_OK = RunResult(
    stdout="OK: docker build and in-container compile succeeded",
    stderr="",
    exit_code=0,
    duration_seconds=0.1,
)


def _patch_verification(*, return_value: RunResult = _VERIFY_OK):  # type: ignore[no-untyped-def]
    """Mock the shared check_docker_build.sh boundary (T014/T015) — a separate
    subprocess call from the run-scoped image build/bind-mounted probing below."""
    return patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=return_value,
    )


def _analysis(
    source_path: Path,
    system_packages: list[str] | None = None,
    *,
    build_system: BuildSystem = BuildSystem.CMAKE,
) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=build_system,
        build_files=[],
        headers=[],
        language=Language.C,
        clone_url=_FAKE_URL,
        repo_ref=None,
        system_packages=system_packages or [],
    )


# check_availability


def test_check_availability_raises_when_docker_info_fails() -> None:
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(
                stdout="",
                stderr="Cannot connect to the Docker daemon",
                exit_code=1,
                duration_seconds=0.1,
            ),
        ),
        pytest.raises(EnvironmentUnavailableError) as exc_info,
    ):
        OssFuzzExecutor().check_availability()
    assert exc_info.value.environment is Environment.OSS_FUZZ
    assert "Docker daemon not reachable" in str(exc_info.value)


def test_check_availability_ok_when_docker_info_succeeds() -> None:
    with patch(
        "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
        return_value=RunResult(
            stdout="Server Version: 24.0", stderr="", exit_code=0, duration_seconds=0.1
        ),
    ):
        OssFuzzExecutor().check_availability()  # must not raise


# run_library_build — probe image build failure classification (T024)


def test_run_library_build_network_failure_raises_environment_unavailable(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(
                stdout="",
                stderr="Error response from daemon: Get https://gcr.io: no such host",
                exit_code=1,
                duration_seconds=0.1,
            ),
        ),
        pytest.raises(EnvironmentUnavailableError) as exc_info,
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)
    assert exc_info.value.environment is Environment.OSS_FUZZ


def test_run_library_build_non_network_probe_failure_returns_failed_result(tmp_path: Path) -> None:
    """A genuine probe-image build failure (bad apt package, etc.) is a stage failure
    eligible for agent fallback — not an EnvironmentUnavailableError (T024)."""
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with patch(
        "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
        return_value=RunResult(
            stdout="",
            stderr="E: Unable to locate package definitely-not-a-real-package",
            exit_code=1,
            duration_seconds=0.1,
        ),
    ):
        result = OssFuzzExecutor().run_library_build(
            _analysis(workdir / "src", ["definitely-not-a-real-package"]), workdir
        )
    assert result.succeeded is False
    assert result.environment is Environment.OSS_FUZZ
    assert "definitely-not-a-real-package" in result.stderr


# run_library_build — happy path tags environment and reuses the probe image


def test_run_library_build_success_tags_environment_oss_fuzz(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        result = OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)
    assert result.succeeded is True
    assert result.environment is Environment.OSS_FUZZ


def test_run_library_build_gates_on_shared_verification_script(tmp_path: Path) -> None:
    """run_library_build's pass/fail comes from check_docker_build.sh (T014, FR-001),
    not from the internal bind-mounted build's own exit code."""
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    verify_failed = RunResult(
        stdout="FAILED: compile (build.sh) or the artifact check failed inside the container",
        stderr="",
        exit_code=1,
        duration_seconds=0.1,
    )
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(return_value=verify_failed) as mock_verify,
    ):
        result = OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    assert result.succeeded is False
    command = mock_verify.call_args[0][0]
    assert command[1].endswith("check_docker_build.sh")
    assert result.command == command


def test_docker_run_invocation_mounts_workdir_and_uses_bash_entrypoint(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ) as mock_streaming,
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    docker_command = mock_streaming.call_args[0][0]
    assert docker_command[0] == "docker"
    assert "--entrypoint" in docker_command
    assert "bash" in docker_command
    assert f"{workdir.resolve()}:{workdir.resolve()}" in docker_command


# _ensure_probe_image — bear is a hard requirement in the probe image (T013, FR-011)


def _capture_dockerfile(dockerfiles: dict[str, str]):  # type: ignore[no-untyped-def]
    def fake_run_command(command: list[str], cwd: Path, _timeout: int) -> RunResult:
        if command[:2] == ["docker", "build"]:
            dockerfiles["text"] = (cwd / "Dockerfile").read_text()
        return RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1)

    return fake_run_command


def test_ensure_probe_image_dockerfile_includes_bear_with_no_system_packages(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    dockerfiles: dict[str, str] = {}
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            side_effect=_capture_dockerfile(dockerfiles),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    assert "bear" in dockerfiles["text"]


def test_ensure_probe_image_dockerfile_includes_bear_alongside_system_packages(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    dockerfiles: dict[str, str] = {}
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            side_effect=_capture_dockerfile(dockerfiles),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src", ["libssl-dev"]), workdir)

    assert "bear" in dockerfiles["text"]
    assert "libssl-dev" in dockerfiles["text"]
    # Both are provisioned in the same apt-get invocation, not a second RUN line.
    assert dockerfiles["text"].count("apt-get install") == 1


# run_harness_compile — requires a prior successful run_library_build on the same instance


def test_run_harness_compile_without_prior_library_build_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        OssFuzzExecutor().run_harness_compile(tmp_path / "install", tmp_path, Language.C)


def test_run_harness_compile_reuses_probe_image_from_library_build(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    install_dir = workdir / "install"

    executor = OssFuzzExecutor()
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        executor.run_library_build(_analysis(workdir / "src"), workdir)

    # explore() (called by run_library_build) resets workdir/install — populate the
    # static-lib fixture only after that stage, matching the real pipeline order.
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")

    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        _patch_verification(),
    ):
        result = executor.run_harness_compile(install_dir, workdir, Language.C)

    assert result.environment is Environment.OSS_FUZZ
    assert result.succeeded is True


def test_run_harness_compile_gates_on_shared_verification_script(tmp_path: Path) -> None:
    """run_harness_compile's pass/fail comes from check_docker_build.sh (T015, FR-001),
    not from discovery's own direct-exec probe result."""
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    install_dir = workdir / "install"

    executor = OssFuzzExecutor()
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        executor.run_library_build(_analysis(workdir / "src"), workdir)

    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")

    verify_failed = RunResult(
        stdout="FAILED: compile (build.sh) or the artifact check failed inside the container",
        stderr="",
        exit_code=1,
        duration_seconds=0.1,
    )
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        _patch_verification(return_value=verify_failed) as mock_verify,
    ):
        result = executor.run_harness_compile(install_dir, workdir, Language.C)

    assert result.succeeded is False
    command = mock_verify.call_args[0][0]
    assert command[1].endswith("check_docker_build.sh")
    assert result.command == command


def test_run_harness_compile_skips_verification_without_static_libs(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    install_dir = workdir / "install"

    executor = OssFuzzExecutor()
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        executor.run_library_build(_analysis(workdir / "src"), workdir)

    with _patch_verification() as mock_verify:
        result = executor.run_harness_compile(install_dir, workdir, Language.C)

    mock_verify.assert_not_called()
    assert result.succeeded is False


# workspace materialization — the workspace is a real, buildable OSS-Fuzz project
# throughout the run, not just in the final output (T019, User Story 2)


def test_run_library_build_materializes_real_project_layout(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    assert (workdir / "Dockerfile").exists()
    assert (workdir / "build.sh").exists()
    assert (workdir / "project.yaml").exists()
    assert (workdir / "harness_source").is_dir()
    assert (workdir / "build_library.sh").exists()
    assert (workdir / "compile_harnesses.sh").exists()


def test_run_library_build_workspace_dockerfile_has_no_git_clone_bind_mount_quirk(
    tmp_path: Path,
) -> None:
    """The workspace Dockerfile is the real one (git clone from clone_url), not a
    synthetic tempdir Dockerfile with no git clone at all (T012/T013 remove the old
    probe-image path)."""
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    content = (workdir / "Dockerfile").read_text()
    assert f"RUN git clone {_FAKE_URL} $SRC/src" in content
    assert "COPY build.sh build_library.sh compile_harnesses.sh $SRC/" in content


def test_probe_image_build_error_class_removed() -> None:
    """T013: the synthetic tempdir Dockerfile / probe-image error type is gone."""
    import harnessbuddy.library_builder.environments.oss_fuzz as oss_fuzz_module

    assert not hasattr(oss_fuzz_module, "_ProbeImageBuildError")


# Docker-gated end-to-end tests (T015, T021) — skipped by default per pyproject.toml's
# docker marker. Under the new architecture (T012), the atomic check_docker_build.sh gate
# does a from-scratch `git clone <analysis.clone_url>` inside the container (FR-002) — so,
# unlike the old synthetic-probe-image design, these need a real, network-clonable
# clone_url whose content actually matches analysis.source_path, not a local fixture
# behind a placeholder URL. Reuses the same small, stable public repos the build_matrix
# suite already depends on network access for.


@pytest.mark.docker
def test_run_library_build_real_docker_end_to_end(tmp_path: Path) -> None:
    import subprocess

    from harnessbuddy.core.repos import RepoSource
    from harnessbuddy.library_builder.analysis import analyze

    source = tmp_path / "src"
    subprocess.run(
        ["git", "clone", "--depth=1", "https://github.com/madler/zlib.git", str(source)],
        check=True,
        capture_output=True,
    )
    workdir = tmp_path / "work"
    analysis = analyze(RepoSource(source_path=source, clone_url=str(source), project_name="zlib"))
    analysis.clone_url = "https://github.com/madler/zlib.git"

    result = OssFuzzExecutor().run_library_build(analysis, workdir)
    assert result.succeeded is True
    assert result.environment is Environment.OSS_FUZZ


@pytest.mark.docker
def test_run_docker_build_independently_passes_after_run_library_build(tmp_path: Path) -> None:
    """T021: a workspace run_library_build leaves behind must independently pass
    `bash agents/scripts/check_docker_build.sh <workspace> <project>` — the same command
    the repair agent is told to run — proving there is exactly one definition of "the
    build passed" (FR-001, SC-001)."""
    import subprocess

    from harnessbuddy.core.repos import RepoSource
    from harnessbuddy.core.subprocesses import run_command
    from harnessbuddy.library_builder.analysis import analyze

    source = tmp_path / "src"
    subprocess.run(
        ["git", "clone", "--depth=1", "https://github.com/madler/zlib.git", str(source)],
        check=True,
        capture_output=True,
    )
    workdir = tmp_path / "work"
    analysis = analyze(RepoSource(source_path=source, clone_url=str(source), project_name="zlib"))
    analysis.clone_url = "https://github.com/madler/zlib.git"

    result = OssFuzzExecutor().run_library_build(analysis, workdir)
    assert result.succeeded is True

    script = (
        Path(__file__).parent.parent.parent.parent / "agents" / "scripts" / "check_docker_build.sh"
    )
    independent = run_command(["bash", str(script), str(workdir.resolve()), "zlib"], workdir, 600)
    assert independent.exit_code == 0, independent.stdout + independent.stderr


@pytest.mark.docker
def test_run_library_build_captures_compile_commands_for_make_fixture(tmp_path: Path) -> None:
    """T014 (US3): the oss-fuzz image guarantees bear (FR-011), so Make/Autotools
    compile-commands capture must succeed there unconditionally — no PATH-dependent
    flakiness the way the local host has. Capture happens during explore()'s own
    bind-mounted run against analysis.source_path, independent of the atomic gate's
    from-scratch clone, so a local fixture with a real, network-clonable clone_url from
    the build_matrix suite (lz4, Makefile-based) is used for both.
    """
    import subprocess

    from harnessbuddy.core.repos import RepoSource
    from harnessbuddy.library_builder.analysis import analyze

    source = tmp_path / "src"
    subprocess.run(
        ["git", "clone", "--depth=1", "https://github.com/lz4/lz4.git", str(source)],
        check=True,
        capture_output=True,
    )
    workdir = tmp_path / "work"
    analysis = analyze(RepoSource(source_path=source, clone_url=str(source), project_name="lz4"))
    analysis.clone_url = "https://github.com/lz4/lz4.git"
    assert analysis.build_system == BuildSystem.MAKEFILE

    result = OssFuzzExecutor().run_library_build(analysis, workdir)
    assert result.succeeded is True
    assert result.environment is Environment.OSS_FUZZ
    assert result.compile_commands_error is None
    assert result.compile_commands_path is not None
    assert result.compile_commands_path == workdir.resolve() / "compile_commands.json"
    entries = json.loads(result.compile_commands_path.read_text())
    assert any(entry["file"].endswith(".c") for entry in entries)
