from __future__ import annotations

from pathlib import Path

import yaml

from harnessbuddy.feature_extractor.benchmark_yaml import generate_benchmark
from harnessbuddy.feature_extractor.extraction import extract_features


def test_generate_benchmark_filters_and_defaults(zlib_feature_test_dir: Path) -> None:
    extracted = extract_features(zlib_feature_test_dir)
    benchmark = generate_benchmark(zlib_feature_test_dir, headers=[])

    assert benchmark.target_name == "default_fuzzer"
    assert benchmark.target_path == "/src/harness_source/default_fuzzer.c"

    names = {f.name for f in benchmark.functions}
    assert "deflate" in names
    assert "inflate" in names
    # deflate_stored is a static helper defined in deflate.c, not part of the public API.
    assert "deflate_stored" not in names
    assert len(benchmark.functions) < len(extracted.functions)

    output_path = zlib_feature_test_dir / f"{benchmark.project}.yaml"
    assert output_path.is_file()
    loaded = yaml.safe_load(output_path.read_text())
    assert loaded["project"] == benchmark.project
    assert loaded["language"] == "c"
    assert loaded["target_name"] == "default_fuzzer"
    assert loaded["target_path"] == "/src/harness_source/default_fuzzer.c"
    assert len(loaded["functions"]) == len(benchmark.functions)


def test_generate_benchmark_respects_overrides(zlib_feature_test_dir: Path) -> None:
    extract_features(zlib_feature_test_dir)
    benchmark = generate_benchmark(
        zlib_feature_test_dir,
        headers=[],
        target_name="my_fuzzer",
        target_path="/src/custom/my_fuzzer.cc",
    )
    assert benchmark.target_name == "my_fuzzer"
    assert benchmark.target_path == "/src/custom/my_fuzzer.cc"
