"""The gate scripts: one definition of "the build passed", and where it runs."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from harnessbuddy.core.resources import agent_script

_CHECK_BUILD = agent_script("check_build.sh")
_CHECK_BUILD_IN_CONTAINER = agent_script("check_build_in_container.sh")
_CHECK_DOCKERFILE_FROM_SCRATCH = agent_script("check_dockerfile_from_scratch.sh")

_BUILD_SH = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    'echo "=== build_library.sh ==="\n'
    '"$SCRIPT_DIR/build_library.sh"\n'
    'echo "=== compile_harnesses.sh ==="\n'
    '"$SCRIPT_DIR/compile_harnesses.sh"\n'
)
_GOOD_BUILD_LIBRARY_SH = (
    "#!/bin/bash\nset -euo pipefail\nmkdir -p install/lib install/include\n"
    "touch install/lib/libfoo.a\ntouch install/include/foo.h\n"
)
_GOOD_COMPILE_HARNESSES_SH = (
    '#!/bin/bash\nset -euo pipefail\nmkdir -p "$OUT"\ntouch "$OUT/harness"\n'
)

_BROKEN_BUILD_LIBRARY_SH = (
    "#!/bin/bash\nset -euo pipefail\necho 'simulated build failure' >&2\nexit 1\n"
)

# The gate runs with `--entrypoint bash` and check_build.sh needs bash builtins (compgen), so
# the stand-in image has to carry a real bash — busybox fails before any assertion runs. Small
# and bash-bearing, in place of the multi-gigabyte OSS-Fuzz base builder, for a fixture that
# never compiles anything.
_BASH_IMAGE = "debian:stable-slim"


def _write_project(work_dir: Path, *, build_library: str, compile_harnesses: str) -> None:
    """A project the gate can run: build.sh invokes the other two, all executable."""
    from harnessbuddy.core.files import write_executable

    write_executable(work_dir / "build.sh", _BUILD_SH)
    write_executable(work_dir / "build_library.sh", build_library)
    write_executable(work_dir / "compile_harnesses.sh", compile_harnesses)


def _run_gate(work_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_CHECK_BUILD), str(work_dir)], capture_output=True, text=True, timeout=60
    )


# check_build.sh


def test_check_build_exits_zero_on_good_project(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        build_library=_GOOD_BUILD_LIBRARY_SH,
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    result = _run_gate(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_check_build_exits_nonzero_on_broken_project(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        build_library=_BROKEN_BUILD_LIBRARY_SH,
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    result = _run_gate(tmp_path)
    assert result.returncode != 0
    assert "simulated build failure" in result.stderr or "FAILED" in result.stderr


def test_check_build_wrong_arg_count_exits_nonzero() -> None:
    result = subprocess.run(["bash", str(_CHECK_BUILD)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Usage" in result.stderr


def test_check_build_defaults_out_to_the_workspace(tmp_path: Path) -> None:
    """The base image defines $OUT and a host shell does not, so the gate supplies the same
    fallback the generated scripts use, or `set -u` aborts it on the host."""
    _write_project(
        tmp_path,
        build_library=_GOOD_BUILD_LIBRARY_SH,
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    assert _run_gate(tmp_path).returncode == 0
    assert (tmp_path / "out" / "harness").exists()


def test_check_build_rebuilds_from_nothing(tmp_path: Path) -> None:
    """build_library.sh exits early when install/ already holds artifacts, so a gate that left
    a previous build in place would assert on artifacts it never produced."""
    _write_project(
        tmp_path,
        build_library=(
            "#!/bin/bash\nset -euo pipefail\n"
            'if [ -e install/lib/libfoo.a ]; then echo "skipped a real build" >&2; exit 0; fi\n'
            "mkdir -p install/lib install/include\n"
            "touch install/lib/libfoo.a install/include/foo.h\n"
        ),
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    (tmp_path / "install" / "lib").mkdir(parents=True)
    (tmp_path / "install" / "lib" / "libfoo.a").write_text("from an earlier build")

    result = _run_gate(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "skipped a real build" not in result.stderr


def test_check_build_fails_when_no_static_library_is_produced(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        build_library=(
            "#!/bin/bash\nset -euo pipefail\n"
            "mkdir -p install/include\ntouch install/include/foo.h\n"
        ),
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    result = _run_gate(tmp_path)
    assert result.returncode != 0
    assert "no static libraries" in result.stderr


def test_check_build_fails_when_no_harness_binary_is_produced(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        build_library=_GOOD_BUILD_LIBRARY_SH,
        compile_harnesses='#!/bin/bash\nset -euo pipefail\nmkdir -p "$OUT"\n',
    )
    result = _run_gate(tmp_path)
    assert result.returncode != 0
    assert "no harness binary" in result.stderr


# stage markers — the combined output attributes a failure to the right stage, even though the
# gate reports one atomic pass/fail


def test_library_failure_output_identifies_the_stage_before_harness(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        build_library=_BROKEN_BUILD_LIBRARY_SH,
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    result = _run_gate(tmp_path)
    combined = result.stdout + result.stderr
    assert "=== build_library.sh ===" in combined
    # compile_harnesses.sh's own marker is never reached — build_library.sh failed first.
    assert "=== compile_harnesses.sh ===" not in combined


def test_harness_failure_output_shows_the_library_succeeded_first(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        build_library=_GOOD_BUILD_LIBRARY_SH,
        compile_harnesses=(
            "#!/bin/bash\nset -euo pipefail\necho 'simulated harness failure' >&2\nexit 1\n"
        ),
    )
    result = _run_gate(tmp_path)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "simulated harness failure" in combined
    assert combined.index("=== build_library.sh ===") < combined.index(
        "=== compile_harnesses.sh ==="
    )


# the container wrappers — require a real Docker daemon


@pytest.mark.docker
def test_check_build_in_container_exits_zero_on_good_project(tmp_path: Path) -> None:
    """The gate script is mounted into the image rather than baked in, so the assertions it runs
    are the ones the host path runs."""
    _write_project(
        tmp_path,
        build_library=_GOOD_BUILD_LIBRARY_SH,
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    (tmp_path / "Dockerfile").write_text(f"FROM {_BASH_IMAGE}\n")
    result = subprocess.run(
        ["bash", str(_CHECK_BUILD_IN_CONTAINER), str(tmp_path), "checkscripttest"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Mounted, so the build products are on the host afterwards.
    assert (tmp_path / "install" / "lib" / "libfoo.a").exists()


@pytest.mark.docker
def test_check_build_in_container_resolves_a_source_symlink_out_of_the_workspace(
    tmp_path: Path,
) -> None:
    """The workspace mount covers /src whole, shadowing the source tree the image cloned there,
    so the container reads <workspace>/src from the host. An oss-fuzz run over a local path
    leaves that a symlink into the user's own tree, which dangles in the container unless its
    target is mounted too."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    source = tmp_path / "elsewhere"
    source.mkdir()
    (source / "foo.h").write_text("int foo(void);\n")
    (workspace / "src").symlink_to(source)

    _write_project(
        workspace,
        build_library=(
            "#!/bin/bash\nset -euo pipefail\n"
            'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            "mkdir -p install/lib install/include\n"
            'cp "$SCRIPT_DIR/src/foo.h" install/include/\n'
            "touch install/lib/libfoo.a\n"
        ),
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    (workspace / "Dockerfile").write_text(f"FROM {_BASH_IMAGE}\n")

    result = subprocess.run(
        ["bash", str(_CHECK_BUILD_IN_CONTAINER), str(workspace), "checkscriptsymlink"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (workspace / "install" / "include" / "foo.h").exists()


# Stands in for the OSS-Fuzz base image's own ENV OUT=/out, which is what puts the harness
# binaries outside the workspace mount.
_OUT_OUTSIDE_WORKSPACE_DOCKERFILE = f"FROM {_BASH_IMAGE}\nENV OUT=/out\n"


@pytest.mark.docker
def test_check_build_in_container_returns_harness_binaries_to_the_host(tmp_path: Path) -> None:
    """The OSS-Fuzz base image defines $OUT=/out, which is not under the /src workspace mount,
    so without a mount of its own the harness binaries are discarded with the container: the
    gate passes and the host has nothing to check. The pipeline then rejects every agent repair
    in this environment, including the ones that worked."""
    _write_project(
        tmp_path,
        build_library=_GOOD_BUILD_LIBRARY_SH,
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    (tmp_path / "Dockerfile").write_text(_OUT_OUTSIDE_WORKSPACE_DOCKERFILE)

    result = subprocess.run(
        ["bash", str(_CHECK_BUILD_IN_CONTAINER), str(tmp_path), "checkscriptoutmount"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    harness = tmp_path / "out" / "harness"
    assert harness.exists()
    # Root-owned artifacts block the next run's rebuild-from-nothing just as install/ does.
    assert harness.stat().st_uid == os.getuid()
    shutil.rmtree(tmp_path / "out")


@pytest.mark.docker
def test_check_build_in_container_clears_stale_output_when_out_is_a_mountpoint(
    tmp_path: Path,
) -> None:
    """The gate rebuilds from nothing, but $OUT cannot be removed once it is a bind
    mountpoint — the container gets "Device or resource busy" and set -e fails the gate before
    the build starts. Its contents have to go while the directory itself stays."""
    _write_project(
        tmp_path,
        build_library=_GOOD_BUILD_LIBRARY_SH,
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    (tmp_path / "Dockerfile").write_text(_OUT_OUTSIDE_WORKSPACE_DOCKERFILE)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "stale_harness").write_text("from an earlier run")

    result = subprocess.run(
        ["bash", str(_CHECK_BUILD_IN_CONTAINER), str(tmp_path), "checkscriptoutstale"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "out" / "stale_harness").exists()
    assert (tmp_path / "out" / "harness").exists()
    shutil.rmtree(tmp_path / "out")


@pytest.mark.docker
def test_check_build_in_container_returns_artifact_ownership_to_the_host(tmp_path: Path) -> None:
    """The image runs as root, so everything it writes into the bind mount lands owned by uid 0.
    Without a chown back, the host user cannot remove the install/ tree the container created,
    and the next run's rebuild-from-nothing fails on it."""
    _write_project(
        tmp_path,
        build_library=_GOOD_BUILD_LIBRARY_SH,
        compile_harnesses=_GOOD_COMPILE_HARNESSES_SH,
    )
    (tmp_path / "Dockerfile").write_text(f"FROM {_BASH_IMAGE}\n")

    result = subprocess.run(
        ["bash", str(_CHECK_BUILD_IN_CONTAINER), str(tmp_path), "checkscriptownership"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    lib_dir = tmp_path / "install" / "lib"
    assert lib_dir.stat().st_uid == os.getuid()
    assert (lib_dir / "libfoo.a").stat().st_uid == os.getuid()
    # The operation the next run makes, and the one that raised PermissionError.
    shutil.rmtree(tmp_path / "install")


@pytest.mark.docker
def test_check_build_in_container_exits_nonzero_when_the_image_fails_to_build(
    tmp_path: Path,
) -> None:
    (tmp_path / "Dockerfile").write_text("FROM busybox\nRUN false\n")
    result = subprocess.run(
        ["bash", str(_CHECK_BUILD_IN_CONTAINER), str(tmp_path), "checkscripttestbroken"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


@pytest.mark.docker
def test_check_dockerfile_from_scratch_exits_nonzero_when_the_image_fails_to_build(
    tmp_path: Path,
) -> None:
    """The check that keeps the mounted gate honest: a broken apt or clone layer fails here
    even though a mounted run would have supplied what the image failed to."""
    (tmp_path / "Dockerfile").write_text("FROM busybox\nRUN false\n")
    result = subprocess.run(
        ["bash", str(_CHECK_DOCKERFILE_FROM_SCRATCH), str(tmp_path), "checkscratchbroken"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
