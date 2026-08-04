from __future__ import annotations

import hashlib
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

    Cached under .harnessbuddy/native-build/build/, keyed to the `clang --version` string and a
    hash of native/'s own sources. A new LLVM version means a rebuild rather than a silent
    reuse against a mismatched LibTooling ABI, and an edit to the tool invalidates the cache
    without a caller having to pass force_rebuild.
    """
    build_dir = default_state_dir() / "native-build" / "build"
    binary_path = build_dir / _BINARY_NAME
    build_key_marker = build_dir / ".build_key"
    current_build_key = f"{_detect_llvm_version()}:{_hash_native_sources()}"
    if (
        not force_rebuild
        and binary_path.exists()
        and build_key_marker.exists()
        and build_key_marker.read_text().strip() == current_build_key
    ):
        return binary_path

    build_dir.mkdir(parents=True, exist_ok=True)
    _configure(build_dir)
    _build(build_dir)

    if not binary_path.exists():
        raise NativeBuildError(
            f"Feature extraction build reported success but {binary_path} was not produced."
        )
    else:
        print("Feature extraction tool successfully built!")
    build_key_marker.write_text(current_build_key)
    return binary_path


def _hash_native_sources() -> str:
    """Hash every file under native/, so any edit to the tool's sources invalidates the
    cached binary."""
    hasher = hashlib.sha256()
    for path in sorted(p for p in _NATIVE_SRC_DIR.rglob("*") if p.is_file()):
        hasher.update(str(path.relative_to(_NATIVE_SRC_DIR)).encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _configure(build_dir: Path) -> None:
    result = run_command_streaming(
        ["cmake", "-S", str(_NATIVE_SRC_DIR), "-B", str(build_dir)], Path.cwd(), timeout=300
    )
    if result.exit_code != 0:
        raise NativeBuildError(
            "=" * 25 + "Feature Configuration Failed" + "=" * 25,
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
            "=" * 25 + "Feature Build Failed" + "=" * 25,
        )


def _detect_llvm_version() -> str:
    result = run_command(["clang", "--version"], Path.cwd(), timeout=10)
    if result.exit_code == 0 and result.stdout:
        return result.stdout.splitlines()[0].strip()
    return "unknown"
