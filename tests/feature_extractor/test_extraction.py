from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from harnessbuddy.feature_extractor import native_build
from harnessbuddy.feature_extractor.extraction import extract_features
from harnessbuddy.library_builder.models import Language


def test_extract_features_finds_zlib_public_api(zlib_feature_test_dir: Path) -> None:
    result = extract_features(zlib_feature_test_dir)

    assert result.language == Language.C
    assert (zlib_feature_test_dir / "features.json").is_file()

    functions = {f.name: f for f in result.functions}
    assert functions["deflate"].is_public_api
    assert [p.type for p in functions["deflate"].params] == ["z_streamp", "int"]
    assert functions["inflate"].is_public_api
    assert [p.type for p in functions["inflate"].params] == ["z_streamp", "int"]

    typedefs = {t.name: t for t in result.typedefs}
    assert "z_stream_s" in typedefs["z_stream"].underlying_type

    records = {r.name: r for r in result.records}
    assert records["z_stream_s"].kind == "struct"
    field_names = {f.name for f in records["z_stream_s"].fields}
    assert "next_in" in field_names
    assert "avail_in" in field_names

    macros = {m.name for m in result.macros}
    assert "ZEXTERN" in macros
    assert "ZEXPORT" in macros


def test_extract_features_twice_overwrites_without_duplicating(zlib_feature_test_dir: Path) -> None:
    extract_features(zlib_feature_test_dir)
    result = extract_features(zlib_feature_test_dir)

    assert list(zlib_feature_test_dir.glob("features.json")) == [
        zlib_feature_test_dir / "features.json"
    ]
    assert result.functions


def test_extract_features_second_run_reuses_cached_native_binary(
    zlib_feature_test_dir: Path,
) -> None:
    extract_features(zlib_feature_test_dir)  # ensure the native binary is already built/cached

    with (
        patch.object(native_build, "_configure") as mock_configure,
        patch.object(native_build, "_build") as mock_build,
    ):
        extract_features(zlib_feature_test_dir)

    mock_configure.assert_not_called()
    mock_build.assert_not_called()
