from __future__ import annotations

from pathlib import Path

import pytest

_ZLIB_FEATURE_TEST_DIR = Path(__file__).parent.parent.parent / "zlib_feature_test"


@pytest.fixture(scope="session", autouse=True)
def _require_zlib_feature_test() -> None:
    """Skip every test in this package if the real zlib fixture isn't set up locally."""
    if not _ZLIB_FEATURE_TEST_DIR.is_dir():
        pytest.skip(
            f"{_ZLIB_FEATURE_TEST_DIR} not found; see specs/006-feature-extractor/"
            "quickstart.md Prerequisites to set it up"
        )
    if not (_ZLIB_FEATURE_TEST_DIR / "compile_commands.json").is_file():
        pytest.skip(
            f"{_ZLIB_FEATURE_TEST_DIR}/compile_commands.json not found; configure with "
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"
        )


@pytest.fixture(scope="session")
def zlib_feature_test_dir() -> Path:
    return _ZLIB_FEATURE_TEST_DIR
