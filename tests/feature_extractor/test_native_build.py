from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from harnessbuddy.feature_extractor import native_build


def _write_fake_native_sources(native_src_dir: Path, *, content: str = "int main() {}") -> None:
    native_src_dir.mkdir(parents=True, exist_ok=True)
    (native_src_dir / "CMakeLists.txt").write_text("cmake stub")
    src_dir = native_src_dir / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "main.cpp").write_text(content)


def _build_that_creates_binary(binary_path: Path):  # type: ignore[no-untyped-def]
    def fake_build(_build_dir: Path) -> None:
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_text("fake binary")

    return fake_build


def test_build_native_tool_reuses_cache_when_sources_and_version_unchanged(
    tmp_path: Path,
) -> None:
    native_src_dir = tmp_path / "native"
    _write_fake_native_sources(native_src_dir)
    state_dir = tmp_path / "state"
    binary_path = state_dir / "native-build" / "build" / native_build._BINARY_NAME

    with (
        patch.object(native_build, "_NATIVE_SRC_DIR", native_src_dir),
        patch.object(native_build, "default_state_dir", return_value=state_dir),
        patch.object(native_build, "_detect_llvm_version", return_value="clang 1.0"),
        patch.object(native_build, "_configure") as mock_configure,
        patch.object(native_build, "_build", side_effect=_build_that_creates_binary(binary_path)),
    ):
        native_build.build_native_tool()  # first call: builds and writes the cache key
        mock_configure.reset_mock()

        with patch.object(native_build, "_build") as mock_build_second:
            result = native_build.build_native_tool()

    mock_configure.assert_not_called()
    mock_build_second.assert_not_called()
    assert result == binary_path


def test_build_native_tool_rebuilds_when_native_source_changes(tmp_path: Path) -> None:
    """A cached binary must not be silently reused if native/'s own sources changed
    since it was built (e.g. a compiler-flag fix in main.cpp) — the LLVM-version-only
    cache key previously missed this, requiring callers to know to pass
    force_rebuild explicitly."""
    native_src_dir = tmp_path / "native"
    _write_fake_native_sources(native_src_dir)
    state_dir = tmp_path / "state"
    binary_path = state_dir / "native-build" / "build" / native_build._BINARY_NAME

    with (
        patch.object(native_build, "_NATIVE_SRC_DIR", native_src_dir),
        patch.object(native_build, "default_state_dir", return_value=state_dir),
        patch.object(native_build, "_detect_llvm_version", return_value="clang 1.0"),
        patch.object(native_build, "_configure"),
        patch.object(native_build, "_build", side_effect=_build_that_creates_binary(binary_path)),
    ):
        native_build.build_native_tool()

    _write_fake_native_sources(native_src_dir, content="int main() { return 1; }")

    with (
        patch.object(native_build, "_NATIVE_SRC_DIR", native_src_dir),
        patch.object(native_build, "default_state_dir", return_value=state_dir),
        patch.object(native_build, "_detect_llvm_version", return_value="clang 1.0"),
        patch.object(native_build, "_configure") as mock_configure,
        patch.object(native_build, "_build", side_effect=_build_that_creates_binary(binary_path)),
    ):
        native_build.build_native_tool()

    mock_configure.assert_called_once()


def test_build_native_tool_force_rebuild_ignores_matching_cache(tmp_path: Path) -> None:
    native_src_dir = tmp_path / "native"
    _write_fake_native_sources(native_src_dir)
    state_dir = tmp_path / "state"
    binary_path = state_dir / "native-build" / "build" / native_build._BINARY_NAME

    with (
        patch.object(native_build, "_NATIVE_SRC_DIR", native_src_dir),
        patch.object(native_build, "default_state_dir", return_value=state_dir),
        patch.object(native_build, "_detect_llvm_version", return_value="clang 1.0"),
        patch.object(native_build, "_configure"),
        patch.object(native_build, "_build", side_effect=_build_that_creates_binary(binary_path)),
    ):
        native_build.build_native_tool()

        with patch.object(native_build, "_configure") as mock_configure_again:
            native_build.build_native_tool(force_rebuild=True)

    mock_configure_again.assert_called_once()


def test_hash_native_sources_changes_when_a_source_file_changes(tmp_path: Path) -> None:
    native_src_dir = tmp_path / "native"
    _write_fake_native_sources(native_src_dir)

    with patch.object(native_build, "_NATIVE_SRC_DIR", native_src_dir):
        original_hash = native_build._hash_native_sources()

    _write_fake_native_sources(native_src_dir, content="int main() { return 1; }")

    with patch.object(native_build, "_NATIVE_SRC_DIR", native_src_dir):
        changed_hash = native_build._hash_native_sources()

    assert original_hash != changed_hash
