from __future__ import annotations

from dataclasses import dataclass, field

from harnessbuddy.library_builder.models import Language

SCHEMA_VERSION = 1


@dataclass
class Param:
    name: str
    type: str


@dataclass
class FunctionSignature:
    name: str
    return_type: str
    params: list[Param]
    signature: str
    is_public_api: bool
    header_path: str


@dataclass
class Typedef:
    name: str
    underlying_type: str
    header_path: str


@dataclass
class MacroDefinition:
    name: str
    is_function_like: bool
    params: list[str]
    value: str
    header_path: str


@dataclass
class Enumerator:
    name: str
    value: int


@dataclass
class EnumDefinition:
    name: str | None
    enumerators: list[Enumerator]
    header_path: str


@dataclass
class Field:
    name: str
    type: str


@dataclass
class StructUnionDefinition:
    name: str | None
    kind: str  # "struct" | "union"
    fields: list[Field]
    header_path: str


@dataclass
class FeatureArtifactSet:
    schema_version: int
    project_name: str
    language: Language
    functions: list[FunctionSignature] = field(default_factory=list)
    typedefs: list[Typedef] = field(default_factory=list)
    macros: list[MacroDefinition] = field(default_factory=list)
    enums: list[EnumDefinition] = field(default_factory=list)
    records: list[StructUnionDefinition] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class BenchmarkFunction:
    name: str
    signature: str
    return_type: str
    params: list[Param]


@dataclass
class BenchmarkYaml:
    project: str
    language: str
    target_name: str
    target_path: str
    functions: list[BenchmarkFunction] = field(default_factory=list)
