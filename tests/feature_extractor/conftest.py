from __future__ import annotations

import json
from pathlib import Path

import pytest

_ZLIB_FEATURE_TEST_DIR = Path(__file__).parent.parent.parent / "zlib_feature_test"

_RECONFIGURE_HINT = (
    "cmake -S zlib_feature_test -B zlib_feature_test -DCMAKE_EXPORT_COMPILE_COMMANDS=ON"
)


def _compile_commands_directory(compile_commands_path: Path) -> str | None:
    """The 'directory' baked into the first compile_commands.json entry, or None if
    the file is empty/malformed."""
    try:
        entries = json.loads(compile_commands_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not entries or not isinstance(entries, list):
        return None
    return entries[0].get("directory")


@pytest.fixture(scope="session", autouse=True)
def _require_zlib_feature_test() -> None:
    """Skip every test in this package if the real zlib fixture isn't set up locally."""
    if not _ZLIB_FEATURE_TEST_DIR.is_dir():
        pytest.skip(
            f"{_ZLIB_FEATURE_TEST_DIR} not found; see specs/006-feature-extractor/"
            "quickstart.md Prerequisites to set it up"
        )
    compile_commands_path = _ZLIB_FEATURE_TEST_DIR / "compile_commands.json"
    if not compile_commands_path.is_file():
        pytest.skip(
            f"{compile_commands_path} not found; configure with -DCMAKE_EXPORT_COMPILE_COMMANDS=ON"
        )
    baked_directory = _compile_commands_directory(compile_commands_path)
    current_directory = str(_ZLIB_FEATURE_TEST_DIR.resolve())
    if baked_directory is not None and baked_directory != current_directory:
        pytest.skip(
            f"{compile_commands_path} was baked on another machine/path "
            f"({baked_directory!r}, expected {current_directory!r}). Regenerate it "
            f"on this host: `{_RECONFIGURE_HINT}`"
        )


@pytest.fixture(scope="session")
def zlib_feature_test_dir() -> Path:
    return _ZLIB_FEATURE_TEST_DIR
