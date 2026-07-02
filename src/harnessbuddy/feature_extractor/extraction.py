from __future__ import annotations

import json
from pathlib import Path

from harnessbuddy.feature_extractor.models import (
    SCHEMA_VERSION,
    EnumDefinition,
    Enumerator,
    FeatureArtifactSet,
    Field,
    FunctionSignature,
    MacroDefinition,
    Param,
    StructUnionDefinition,
    Typedef,
)
from harnessbuddy.library_builder.models import Language

_REQUIRED_FIELDS = (
    "schema_version",
    "project_name",
    "language",
    "functions",
    "typedefs",
    "macros",
    "enums",
    "records",
    "warnings",
)

_LANGUAGE_BY_VALUE = {lang.value: lang for lang in Language if lang != Language.UNKNOWN}


class FeatureArtifactError(Exception):
    """The feature artifact JSON is missing, malformed, or from an incompatible schema."""


class MissingCompileCommandsError(Exception):
    """The output directory has no compile_commands.json to extract features from."""


class MissingFeatureArtifactError(Exception):
    """The output directory has no features.json; extract-features must run first."""


def extract_features(output_dir: Path) -> FeatureArtifactSet:
    """Extract a library's declarations into features.json, returning the parsed result.

    Resolves <output_dir>/compile_commands.json (FR-003), builds/invokes the native
    tool, and overwrites <output_dir>/features.json with its result (FR-010, FR-015).
    """
    from harnessbuddy.core.subprocesses import run_command_streaming
    from harnessbuddy.feature_extractor.native_build import build_native_tool

    compile_commands = output_dir / "compile_commands.json"
    if not compile_commands.is_file():
        raise MissingCompileCommandsError(
            f"{compile_commands} not found. Generate one with CMake "
            "(-DCMAKE_EXPORT_COMPILE_COMMANDS=ON) or by wrapping a non-CMake build "
            "with 'bear -- <build command>'."
        )

    binary = build_native_tool()
    features_json = output_dir / "features.json"
    project_name = output_dir.resolve().name
    result = run_command_streaming(
        [str(binary.resolve()), str(output_dir.resolve()), str(features_json), project_name],
        Path.cwd(),
        timeout=600,
    )
    if result.exit_code != 0:
        raise FeatureArtifactError(
            f"Native feature_extractor tool failed (exit code {result.exit_code}):\n{result.stdout}"
        )
    return load_feature_artifact(features_json)


def load_feature_artifact(path: Path) -> FeatureArtifactSet:
    """Parse and validate a features.json file into a typed FeatureArtifactSet.

    Raises FeatureArtifactError with an actionable message on a missing file,
    malformed JSON, missing/mistyped fields, or a schema_version mismatch.
    """
    if not path.exists():
        raise FeatureArtifactError(
            f"{path} does not exist. Run 'harnessbuddy extract-features' first."
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise FeatureArtifactError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise FeatureArtifactError(f"{path} does not contain a JSON object.")

    project_name, language = _validate_header_fields(path, data)

    try:
        return FeatureArtifactSet(
            schema_version=data["schema_version"],
            project_name=project_name,
            language=language,
            functions=[_parse_function(f) for f in data["functions"]],
            typedefs=[_parse_typedef(t) for t in data["typedefs"]],
            macros=[_parse_macro(m) for m in data["macros"]],
            enums=[_parse_enum(e) for e in data["enums"]],
            records=[_parse_record(r) for r in data["records"]],
            warnings=[str(w) for w in data["warnings"]],
        )
    except (KeyError, TypeError) as exc:
        raise FeatureArtifactError(f"{path} has a malformed entry: {exc}") from exc


def _validate_header_fields(path: Path, data: dict) -> tuple[str, Language]:
    """Validate schema_version/project_name/language, returning the latter two."""
    missing = [key for key in _REQUIRED_FIELDS if key not in data]
    if missing:
        raise FeatureArtifactError(f"{path} is missing required field(s): {', '.join(missing)}")

    if data["schema_version"] != SCHEMA_VERSION:
        raise FeatureArtifactError(
            f"{path} has schema_version {data['schema_version']!r}, "
            f"expected {SCHEMA_VERSION}. Re-run 'harnessbuddy extract-features'."
        )

    project_name = data["project_name"]
    if not isinstance(project_name, str) or not project_name:
        raise FeatureArtifactError(f"{path} has an empty or non-string project_name.")

    language = _LANGUAGE_BY_VALUE.get(data["language"])
    if language is None:
        raise FeatureArtifactError(f"{path} has an unrecognized language: {data['language']!r}")

    return project_name, language


def _parse_function(data: dict) -> FunctionSignature:
    return FunctionSignature(
        name=data["name"],
        return_type=data["return_type"],
        params=[Param(name=p["name"], type=p["type"]) for p in data["params"]],
        signature=data["signature"],
        is_public_api=data["is_public_api"],
        header_path=data["header_path"],
    )


def _parse_typedef(data: dict) -> Typedef:
    return Typedef(
        name=data["name"],
        underlying_type=data["underlying_type"],
        header_path=data["header_path"],
    )


def _parse_macro(data: dict) -> MacroDefinition:
    return MacroDefinition(
        name=data["name"],
        is_function_like=data["is_function_like"],
        params=[str(p) for p in data["params"]],
        value=data["value"],
        header_path=data["header_path"],
    )


def _parse_enum(data: dict) -> EnumDefinition:
    return EnumDefinition(
        name=data["name"],
        enumerators=[Enumerator(name=e["name"], value=e["value"]) for e in data["enumerators"]],
        header_path=data["header_path"],
    )


def _parse_record(data: dict) -> StructUnionDefinition:
    return StructUnionDefinition(
        name=data["name"],
        kind=data["kind"],
        fields=[Field(name=f["name"], type=f["type"]) for f in data["fields"]],
        header_path=data["header_path"],
    )
