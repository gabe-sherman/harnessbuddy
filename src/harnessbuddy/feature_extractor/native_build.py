from __future__ import annotations

import multiprocessing
from pathlib import Path

from harnessbuddy.core.paths import default_state_dir
from harnessbuddy.core.subprocesses import run_command, run_command_streaming

_NATIVE_SRC_DIR = Path(__file__).parent / "native"
_BINARY_NAME = "feature_extractor"


class NativeBuildError(Exception):
    """The native feature_extractor tool failed to configure or build."""


def build_native_tool(*, force_rebuild: bool = False) -> Path:
    """Build (or reuse a cached build of) the native feature_extractor binary.

    Cached under .harnessbuddy/native-build/build/, keyed to the LLVM/Clang version
    string reported by `clang --version` at build time (research.md §2) — if that
    version changes, the cache is invalidated and the tool is rebuilt rather than
    silently reused against a mismatched LibTooling ABI.
    """
    build_dir = default_state_dir() / "native-build" / "build"
    binary_path = build_dir / _BINARY_NAME
    version_marker = build_dir / ".llvm_version"
    current_version = _detect_llvm_version()

    if (
        not force_rebuild
        and binary_path.exists()
        and version_marker.exists()
        and version_marker.read_text().strip() == current_version
    ):
        return binary_path

    build_dir.mkdir(parents=True, exist_ok=True)
    _configure(build_dir)
    _build(build_dir)

    if not binary_path.exists():
        raise NativeBuildError(f"Native build reported success but {binary_path} was not produced.")
    version_marker.write_text(current_version)
    return binary_path


def _configure(build_dir: Path) -> None:
    result = run_command_streaming(
        ["cmake", "-S", str(_NATIVE_SRC_DIR), "-B", str(build_dir)], Path.cwd(), timeout=300
    )
    if result.exit_code != 0:
        raise NativeBuildError(
            "Failed to configure the native feature_extractor tool (cmake). This "
            "usually means LLVM/Clang development packages (headers plus the "
            "LibTooling static libraries) are not installed, so "
            "find_package(Clang)/find_package(LLVM) could not locate a usable "
            f"installation:\n{result.stdout}"
        )


def _build(build_dir: Path) -> None:
    jobs = str(multiprocessing.cpu_count())
    result = run_command_streaming(
        ["cmake", "--build", str(build_dir), "--target", _BINARY_NAME, "-j", jobs],
        Path.cwd(),
        timeout=1800,
    )
    if result.exit_code != 0:
        raise NativeBuildError(
            f"Failed to build the native feature_extractor tool:\n{result.stdout}"
        )


def _detect_llvm_version() -> str:
    result = run_command(["clang", "--version"], Path.cwd(), timeout=10)
    if result.exit_code == 0 and result.stdout:
        return result.stdout.splitlines()[0].strip()
    return "unknown"
