from __future__ import annotations

from pathlib import Path

import yaml

from harnessbuddy.feature_extractor.extraction import (
    MissingFeatureArtifactError,
    load_feature_artifact,
)
from harnessbuddy.feature_extractor.models import BenchmarkFunction, BenchmarkYaml, FunctionSignature
from harnessbuddy.library_builder.models import Language

_DEFAULT_TARGET_NAME = "default_fuzzer"
_INCLUDE_HEADERS = []
_EXT_BY_LANGUAGE = {Language.C: "c", Language.CPP: "cc"}

def select_public(f: FunctionSignature) -> bool:
    return f.is_public_api

def select_by_header(f: FunctionSignature) -> bool:
    return Path(f.header_path).name in _INCLUDE_HEADERS


def generate_benchmark(
    output_dir: Path, headers: list[str], target_name: str | None = None, target_path: str | None = None
) -> BenchmarkYaml:
    """Convert <output_dir>/features.json into an oss-fuzz-gen-compatible YAML benchmark.

    Filters functions to is_public_api == true (FR-012), defaulting target_name/
    target_path per FR-013/FR-014 unless overridden, and overwrites
    <output_dir>/<project_name>.yaml (FR-015).
    """
    check_include_func = select_public
    if len(headers) > 0:
        global _INCLUDE_HEADERS
        _INCLUDE_HEADERS = headers
        check_include_func = select_by_header
    features_json = output_dir / "features.json"
    if not features_json.is_file():
        raise MissingFeatureArtifactError(
            f"{features_json} not found. Run 'harnessbuddy extract-features' first."
        )
    artifact = load_feature_artifact(features_json)

    resolved_target_name = target_name or _DEFAULT_TARGET_NAME
    ext = _EXT_BY_LANGUAGE.get(artifact.language, "c")
    resolved_target_path = target_path or f"/src/harness_source/{resolved_target_name}.{ext}"

    benchmark = BenchmarkYaml(
        project=artifact.project_name,
        language=artifact.language.value,
        target_name=resolved_target_name,
        target_path=resolved_target_path,
        functions=[
            BenchmarkFunction(
                name=f.name, signature=f.signature, return_type=f.return_type, params=f.params
            )
            for f in artifact.functions
            if check_include_func(f)
        ],
    )

    output_path = output_dir / f"{artifact.project_name}.yaml"
    output_path.write_text(yaml.safe_dump(_to_yaml_dict(benchmark), sort_keys=False))
    return benchmark


def _to_yaml_dict(benchmark: BenchmarkYaml) -> dict:
    return {
        "project": benchmark.project,
        "language": benchmark.language,
        "target_name": benchmark.target_name,
        "target_path": benchmark.target_path,
        "functions": [
            {
                "name": f.name,
                "signature": f.signature,
                "return_type": f.return_type,
                "params": [{"name": p.name, "type": p.type} for p in f.params],
            }
            for f in benchmark.functions
        ],
    }
