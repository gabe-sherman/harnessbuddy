from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "agents" / "scripts"
_CHECK_LOCAL_BUILD = _SCRIPTS_DIR / "check_local_build.sh"
_CHECK_DOCKER_BUILD = _SCRIPTS_DIR / "check_docker_build.sh"

_GOOD_BUILD_LIBRARY_SH = (
    "#!/bin/bash\nset -euo pipefail\nmkdir -p install/lib install/include\n"
    "touch install/lib/libfoo.a\ntouch install/include/foo.h\n"
)
_GOOD_COMPILE_HARNESSES_SH = "#!/bin/bash\nset -euo pipefail\nmkdir -p out\ntouch out/harness\n"
_BROKEN_BUILD_LIBRARY_SH = (
    "#!/bin/bash\nset -euo pipefail\necho 'simulated build failure' >&2\nexit 1\n"
)


def _write_good_project(work_dir: Path) -> None:
    (work_dir / "build_library.sh").write_text(_GOOD_BUILD_LIBRARY_SH)
    (work_dir / "compile_harnesses.sh").write_text(_GOOD_COMPILE_HARNESSES_SH)


def _write_broken_project(work_dir: Path) -> None:
    (work_dir / "build_library.sh").write_text(_BROKEN_BUILD_LIBRARY_SH)
    (work_dir / "compile_harnesses.sh").write_text(_GOOD_COMPILE_HARNESSES_SH)


# check_local_build.sh (T028)


def test_check_local_build_exits_zero_on_good_project(tmp_path: Path) -> None:
    _write_good_project(tmp_path)
    result = subprocess.run(
        ["bash", str(_CHECK_LOCAL_BUILD), str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_check_local_build_exits_nonzero_on_broken_project(tmp_path: Path) -> None:
    _write_broken_project(tmp_path)
    result = subprocess.run(
        ["bash", str(_CHECK_LOCAL_BUILD), str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "simulated build failure" in result.stderr or "FAILED" in result.stderr


def test_check_local_build_wrong_arg_count_exits_nonzero() -> None:
    result = subprocess.run(
        ["bash", str(_CHECK_LOCAL_BUILD)], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "Usage" in result.stderr


# check_docker_build.sh (T029) — requires a real Docker daemon


@pytest.mark.docker
def test_check_docker_build_exits_zero_on_good_project(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text(
        "FROM busybox\nRUN mkdir -p /out && echo hi > /out/hello\n"
    )
    result = subprocess.run(
        ["bash", str(_CHECK_DOCKER_BUILD), str(tmp_path), "checkscripttest"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.docker
def test_check_docker_build_exits_nonzero_on_broken_project(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM busybox\nRUN false\n")
    result = subprocess.run(
        ["bash", str(_CHECK_DOCKER_BUILD), str(tmp_path), "checkscripttestbroken"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
