from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.base import (
    Environment,
    EnvironmentUnavailableError,
)
from harnessbuddy.library_builder.environments.oss_fuzz import (
    OssFuzzExecutor,
    _rewrite_compile_commands_paths,
)
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildSystem,
    Language,
)

_FAKE_URL = "https://github.com/example/testlib.git"

_VERIFY_OK = RunResult(
    stdout="OK: check_build.sh succeeded inside the container",
    stderr="",
    exit_code=0,
    duration_seconds=0.1,
)


def _patch_verification(*, return_value: RunResult = _VERIFY_OK):  # type: ignore[no-untyped-def]
    """Mock the shared gate boundary, a separate subprocess call from the run-scoped image
    build and the bind-mounted probing below."""
    return patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=return_value,
    )


def _analysis(
    source_path: Path,
    *,
    build_system: BuildSystem = BuildSystem.CMAKE,
) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=build_system,
        language=Language.C,
        clone_url=_FAKE_URL,
        repo_ref=None,
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


# run_library_build — probe image build failure classification


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
    """A genuine probe-image build failure (a bad apt package, say) is a stage failure
    eligible for agent fallback, not an EnvironmentUnavailableError."""
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
        result = OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        result = OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)
    assert result.succeeded is True
    assert result.environment is Environment.OSS_FUZZ


def test_run_library_build_does_not_run_shared_verification_script(tmp_path: Path) -> None:
    """run_library_build's pass/fail comes from the bind-mounted probe alone. The gate runs
    once, at the end of run_harness_compile, which validates build_library.sh and
    compile_harnesses.sh together; running it per stage would rebuild the library twice."""
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification() as mock_verify,
    ):
        result = OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    mock_verify.assert_not_called()
    assert result.succeeded is True
    assert result.command == ["bash", "build_library.sh"]


def test_run_library_build_skips_verification_when_probe_fails(tmp_path: Path) -> None:
    """A failing probe short-circuits: the gate's from-scratch `docker build` and `compile`
    is not paid for to reconfirm a failure the probe already reproduced."""
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(
                stdout="CMake Error: something went wrong",
                stderr="",
                exit_code=1,
                duration_seconds=0.1,
            ),
        ),
        _patch_verification() as mock_verify,
    ):
        result = OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    mock_verify.assert_not_called()
    assert result.succeeded is False
    assert result.command == ["bash", "build_library.sh"]


def test_run_harness_compile_skips_verification_when_discovery_fails(tmp_path: Path) -> None:
    """Discovery already exhausted its retries against this install/ output, so the gate
    would only reconfirm the same failure at the cost of another docker build and compile."""
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        executor.run_library_build(_analysis(workdir / "src"), workdir)

    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")

    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(
                stdout="", stderr="link failed", exit_code=1, duration_seconds=0.1
            ),
        ),
        _patch_verification() as mock_verify,
    ):
        result = executor.run_harness_compile(install_dir, workdir, Language.C)

    mock_verify.assert_not_called()
    assert result.succeeded is False
    assert result.command[1].endswith("check_build_in_container.sh")


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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    docker_command = mock_streaming.call_args[0][0]
    assert docker_command[0] == "docker"
    assert "--entrypoint" in docker_command
    assert "bash" in docker_command
    # Mounted at /src, not workdir's own host path: oss-fuzz tooling is hardwired to it.
    assert f"{workdir.resolve()}:/src" in docker_command
    assert "-w" in docker_command
    assert docker_command[docker_command.index("-w") + 1] == "/src"


# _rewrite_compile_commands_paths — container /src paths rewritten to the host workdir, since
# the native feature extractor runs as a host subprocess and fatal-errors trying to chdir into
# a /src path that does not exist there.


def test_rewrite_compile_commands_paths_rewrites_directory_and_file(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    compile_commands = workdir / "compile_commands.json"
    compile_commands.write_text(
        json.dumps(
            [
                {
                    "directory": "/src/build/libfoo",
                    "file": "/src/libfoo/foo.c",
                    "command": "/usr/bin/cc -c /src/libfoo/foo.c -o foo.o",
                }
            ]
        )
    )

    _rewrite_compile_commands_paths(compile_commands, workdir)

    host_prefix = str(workdir.resolve())
    entry = json.loads(compile_commands.read_text())[0]
    assert entry["directory"] == f"{host_prefix}/build/libfoo"
    assert entry["file"] == f"{host_prefix}/libfoo/foo.c"
    assert entry["command"] == f"/usr/bin/cc -c {host_prefix}/libfoo/foo.c -o foo.o"


def test_rewrite_compile_commands_paths_rewrites_glued_flag_prefixes(tmp_path: Path) -> None:
    """-I/src/..., -isystem/src/..., --sysroot=/src/... glue the path straight onto the flag
    with no separator, which is the common case for include and library search paths."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    compile_commands = workdir / "compile_commands.json"
    compile_commands.write_text(
        json.dumps(
            [
                {
                    "directory": "/src/build",
                    "file": "/src/foo.c",
                    "arguments": [
                        "cc",
                        "-I/src/install/include",
                        "-isystem/src/vendor/include",
                        "--sysroot=/src/sysroot",
                        "-c",
                        "/src/foo.c",
                        "-o",
                        "/src/build/foo.o",
                    ],
                }
            ]
        )
    )

    _rewrite_compile_commands_paths(compile_commands, workdir)

    host_prefix = str(workdir.resolve())
    entry = json.loads(compile_commands.read_text())[0]
    assert entry["arguments"] == [
        "cc",
        f"-I{host_prefix}/install/include",
        f"-isystem{host_prefix}/vendor/include",
        f"--sysroot={host_prefix}/sysroot",
        "-c",
        f"{host_prefix}/foo.c",
        "-o",
        f"{host_prefix}/build/foo.o",
    ]


def test_rewrite_compile_commands_paths_leaves_unrelated_paths_alone(tmp_path: Path) -> None:
    """A path that merely contains "/src" as a nested subdirectory, rather than as the
    container mount root, must not be rewritten."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    compile_commands = workdir / "compile_commands.json"
    original = [
        {
            "directory": "/opt/otherproject/src/build",
            "file": "relative/foo.c",
            "command": "cc -I/opt/otherproject/src/include -c relative/foo.c",
        }
    ]
    compile_commands.write_text(json.dumps(original))

    _rewrite_compile_commands_paths(compile_commands, workdir)

    assert json.loads(compile_commands.read_text()) == original


def test_rewrite_compile_commands_paths_no_op_when_no_src_paths_present(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    compile_commands = workdir / "compile_commands.json"
    original_text = json.dumps(
        [{"directory": str(workdir), "file": "foo.c", "command": "cc foo.c"}]
    )
    compile_commands.write_text(original_text)

    _rewrite_compile_commands_paths(compile_commands, workdir)

    assert compile_commands.read_text() == original_text


def test_run_library_build_rewrites_src_paths_in_captured_compile_commands(
    tmp_path: Path,
) -> None:
    """End-to-end wiring: the compile_commands.json run_library_build captures from the
    docker-mounted cmake reconfigure has its /src paths rewritten before it is returned."""
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)

    def fake_run_command_streaming(command: list[str], cwd: Path, _timeout: int) -> RunResult:
        joined = command[-1] if command else ""
        if "CMAKE_EXPORT_COMPILE_COMMANDS" in joined:
            build_dir = cwd / "build"
            build_dir.mkdir(parents=True, exist_ok=True)
            (build_dir / "compile_commands.json").write_text(
                json.dumps(
                    [
                        {
                            "directory": "/src/build",
                            "file": "/src/src/foo.c",
                            "command": "cc -I/src/install/include -c /src/src/foo.c",
                        }
                    ]
                )
            )
        return RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1)

    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            side_effect=fake_run_command_streaming,
        ),
        patch(
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        result = OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    assert result.compile_commands_path is not None
    host_prefix = str(workdir.resolve())
    entry = json.loads(result.compile_commands_path.read_text())[0]
    assert entry["directory"] == f"{host_prefix}/build"
    assert entry["file"] == f"{host_prefix}/src/foo.c"
    assert entry["command"] == f"cc -I{host_prefix}/install/include -c {host_prefix}/src/foo.c"


# _ensure_probe_image — bear is a hard requirement in the probe image


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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    assert "bear" in dockerfiles["text"]


def test_probe_image_uses_the_requested_base_image(tmp_path: Path) -> None:
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        OssFuzzExecutor(base_image="example.com/base:v1").run_library_build(
            _analysis(workdir / "src"), workdir
        )

    assert dockerfiles["text"].startswith("FROM example.com/base:v1\n")


def test_a_base_image_without_compile_is_rejected_as_unavailable(tmp_path: Path) -> None:
    """--base-image selects among OSS-Fuzz base images, not any image: every stage enters the
    build through `compile`, and the generated Dockerfile is written in terms of $SRC. Reported
    as unavailable rather than as a build failure, since no agent edit can add `compile`."""
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)

    def docker(command: list[str], _cwd: Path, _timeout: int) -> RunResult:
        found = "command -v compile" not in command
        return RunResult(stdout="", stderr="", exit_code=0 if found else 1, duration_seconds=0.1)

    with (
        patch("harnessbuddy.library_builder.environments.oss_fuzz.run_command", side_effect=docker),
        pytest.raises(EnvironmentUnavailableError, match="no `compile` on PATH"),
    ):
        OssFuzzExecutor(base_image="debian:stable-slim").run_library_build(
            _analysis(workdir / "src"), workdir
        )


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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        executor.run_library_build(_analysis(workdir / "src"), workdir)

    # explore() resets workdir/install, so the static-lib fixture is populated only after
    # that stage, matching the real pipeline order.
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
    """run_harness_compile's pass/fail comes from check_build_in_container.sh, not from
    discovery's own direct-exec probe result."""
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
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
    assert command[1].endswith("check_build_in_container.sh")
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        executor.run_library_build(_analysis(workdir / "src"), workdir)

    with _patch_verification() as mock_verify:
        result = executor.run_harness_compile(install_dir, workdir, Language.C)

    mock_verify.assert_not_called()
    assert result.succeeded is False


# workspace materialization — the workspace is a real, buildable OSS-Fuzz project throughout
# the run, not just in the final output


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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
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
    assert (workdir / "compile_harness.sh").exists()
    assert (workdir / "compile_harnesses.sh").exists()


def test_run_library_build_workspace_dockerfile_has_no_git_clone_bind_mount_quirk(
    tmp_path: Path,
) -> None:
    """The workspace Dockerfile is the real one, cloning from clone_url, not a synthetic
    tempdir Dockerfile with no git clone at all."""
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        _patch_verification(),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    content = (workdir / "Dockerfile").read_text()
    assert f"RUN git clone --recursive {_FAKE_URL} $SRC/src" in content
    assert "COPY build.sh build_library.sh compile_harness.sh compile_harnesses.sh $SRC/" in content


def test_probe_image_build_error_class_removed() -> None:
    """The synthetic tempdir Dockerfile's probe-image error type is gone."""
    import harnessbuddy.library_builder.environments.oss_fuzz as oss_fuzz_module

    assert not hasattr(oss_fuzz_module, "_ProbeImageBuildError")


# Docker-gated end-to-end tests, skipped by default per pyproject.toml's docker marker. The
# gate clones analysis.clone_url from scratch inside the container, so these need a real,
# network-clonable URL whose content matches analysis.source_path — the same small, stable
# public repos the build_matrix suite already needs network access for.


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
    analysis = dataclasses.replace(analysis, clone_url="https://github.com/madler/zlib.git")

    result = OssFuzzExecutor().run_library_build(analysis, workdir)
    assert result.succeeded is True
    assert result.environment is Environment.OSS_FUZZ


@pytest.mark.docker
def test_run_docker_build_independently_passes_after_run_library_build(tmp_path: Path) -> None:
    """A workspace run_library_build leaves behind must independently pass
    `bash agents/scripts/check_build_in_container.sh <workspace> <project>` — the same command a
    repair agent is told to run — so there is exactly one definition of "the build passed"."""
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
    analysis = dataclasses.replace(analysis, clone_url="https://github.com/madler/zlib.git")

    result = OssFuzzExecutor().run_library_build(analysis, workdir)
    assert result.succeeded is True

    from harnessbuddy.core.resources import agent_script

    script = agent_script("check_build_in_container.sh")
    independent = run_command(["bash", str(script), str(workdir.resolve()), "zlib"], workdir, 600)
    assert independent.exit_code == 0, independent.stdout + independent.stderr


@pytest.mark.docker
def test_run_harness_compile_real_docker_end_to_end(tmp_path: Path) -> None:
    """The harness stage, which nothing else in the docker suite reaches. It passes only
    because the probe enters the build through `compile`: running compile_harnesses.sh directly
    in that image fails on the /usr/lib/libFuzzingEngine.a the ENV names but nothing created
    yet."""
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
    analysis = dataclasses.replace(analysis, clone_url="https://github.com/madler/zlib.git")

    executor = OssFuzzExecutor()
    assert executor.run_library_build(analysis, workdir).succeeded is True

    result = executor.run_harness_compile(workdir.resolve() / "install", workdir, analysis.language)
    assert result.succeeded is True, result.stdout + result.stderr


@pytest.mark.docker
def test_run_library_build_captures_compile_commands_for_make_fixture(tmp_path: Path) -> None:
    """The oss-fuzz image always has bear, so Make/Autotools compile-commands capture must
    succeed there unconditionally, without the local host's PATH-dependent flakiness.

    Capture happens during explore()'s bind-mounted run against analysis.source_path,
    independent of the gate's from-scratch clone, so lz4 serves as both.
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
    analysis = dataclasses.replace(analysis, clone_url="https://github.com/lz4/lz4.git")
    assert analysis.build_system == BuildSystem.MAKEFILE

    result = OssFuzzExecutor().run_library_build(analysis, workdir)
    assert result.succeeded is True
    assert result.environment is Environment.OSS_FUZZ
    assert result.compile_commands_error is None
    assert result.compile_commands_path is not None
    assert result.compile_commands_path == workdir.resolve() / "compile_commands.json"
    entries = json.loads(result.compile_commands_path.read_text())
    assert any(entry["file"].endswith(".c") for entry in entries)


# compiler settings reach the container — `docker run` does not forward the host process
# environment BuildParameters sets, so they have to be passed explicitly.


def _docker_environment(command: list[str]) -> dict[str, str]:
    """The -e NAME=VALUE pairs in a docker run argv."""
    pairs = [command[i + 1] for i, token in enumerate(command) if token == "-e"]
    return dict(pair.split("=", 1) for pair in pairs)


def test_library_build_forwards_the_chosen_compiler_settings(tmp_path: Path) -> None:
    import dataclasses

    from harnessbuddy.library_builder.build_parameters import BuildParameters

    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    parameters = dataclasses.replace(
        BuildParameters.defaults(), cc="gcc", library_cflags="-O2 -DLIBRARY_FLAG"
    )
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
    ):
        OssFuzzExecutor().run_library_build(
            _analysis(workdir / "src"), workdir, parameters=parameters
        )

    forwarded = _docker_environment(mock_streaming.call_args[0][0])
    assert forwarded["CC"] == "gcc"
    assert forwarded["CFLAGS"] == "-O2 -DLIBRARY_FLAG"


def test_library_build_forwards_nothing_the_caller_did_not_choose(tmp_path: Path) -> None:
    """Forwarding an empty CFLAGS would replace the base image's sanitizer configuration with
    nothing, which is worse than not forwarding at all."""
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    assert _docker_environment(mock_streaming.call_args[0][0]) == {}


def test_harness_compile_does_not_replace_the_images_sanitizer_flags(tmp_path: Path) -> None:
    """The default harness flags are just libFuzzer's engine flag, and forwarding them as
    CFLAGS would drop the image's sanitizer configuration and build an unsanitized harness.
    $LIB_FUZZING_ENGINE supplies the engine in the container."""
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
    ):
        executor.run_library_build(_analysis(workdir / "src"), workdir)

    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ) as mock_streaming,
        _patch_verification(),
    ):
        executor.run_harness_compile(install_dir, workdir, Language.C)

    assert "CFLAGS" not in _docker_environment(mock_streaming.call_args[0][0])
