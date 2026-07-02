# Phase 1 Data Model: Library Feature Extraction for Fuzz Target Generation

All entities below are represented as typed Python dataclasses in
`src/harnessbuddy/feature_extractor/models.py`, loaded from the native
tool's JSON output (see `contracts/feature-artifact.schema.json`) rather than
passed around as loose dicts, per Constitution Principle II.

## FeatureArtifactSet

The complete, maximal extraction result for one library. Root of the JSON
artifact (FR-010).

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `int` | Starts at `1`; bumped on any breaking field change (research.md §4). |
| `project_name` | `str` | Matches `AnalysisResult.project_name` from `library_builder`. |
| `language` | `Language` (existing enum: `c`, `cpp`) | Reused from `library_builder.models`, not redefined. |
| `functions` | `list[FunctionSignature]` | Every function declaration visible in the compilation database (FR-004). |
| `typedefs` | `list[Typedef]` | FR-005. |
| `macros` | `list[MacroDefinition]` | FR-006. |
| `enums` | `list[EnumDefinition]` | FR-007. |
| `records` | `list[StructUnionDefinition]` | Structs and unions (FR-008). |
| `warnings` | `list[str]` | Non-fatal issues encountered (stale compile_commands.json entries, skipped files) — spec edge cases. |

**Validation rules**: `schema_version`, `project_name`, and `language` are
required and non-empty. All list fields are required but may be empty (e.g.
a header-only constants library with no functions — spec edge case).
Deduplication (a declaration visible from more than one translation unit
appears once — spec edge case) is enforced by the native tool before JSON is
written, not re-derived in Python.

## FunctionSignature

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | |
| `return_type` | `str` | Textual type, as rendered by Clang's `QualType::getAsString()`. |
| `params` | `list[Param]` | Ordered; `Param` is `{name: str, type: str}` (name may be `""` for unnamed parameters, matching oss-fuzz-gen's own `Benchmark` convention seen in `croaring.yaml`). |
| `signature` | `str` | Full textual signature, e.g. `"roaring_bitmap_t * roaring_bitmap_add_offset(const roaring_bitmap_t *, int64_t)"`. |
| `is_public_api` | `bool` | Derived per research.md §5 (external linkage + declared in a library header). Drives FR-012's YAML filtering. |
| `header_path` | `str` | Path (relative to the library's source root) of the declaring header or source file. |

## Typedef

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | |
| `underlying_type` | `str` | |
| `header_path` | `str` | |

## MacroDefinition

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | |
| `is_function_like` | `bool` | |
| `params` | `list[str]` | Empty when `is_function_like` is `False`. |
| `value` | `str` | Replacement text as written; empty string for macros with no replacement list (e.g. include guards). |
| `header_path` | `str` | |

## EnumDefinition

| Field | Type | Notes |
|---|---|---|
| `name` | `str \| None` | `None` for anonymous enums. |
| `enumerators` | `list[Enumerator]` | `Enumerator` is `{name: str, value: int}`. |
| `header_path` | `str` | |

## StructUnionDefinition

| Field | Type | Notes |
|---|---|---|
| `name` | `str \| None` | Tag name; `None` for anonymous structs/unions. |
| `kind` | `Literal["struct", "union"]` | |
| `fields` | `list[Field]` | `Field` is `{name: str, type: str}`. |
| `header_path` | `str` | |

## BenchmarkYaml

The curated, derived structure written by `benchmark_yaml.py` (FR-011),
matching oss-fuzz-gen's `Benchmark.to_yaml` output shape
(`experiment/benchmark.py` in oss-fuzz-gen).

| Field | Type | Notes |
|---|---|---|
| `project` | `str` | From `FeatureArtifactSet.project_name`. |
| `language` | `str` | From `FeatureArtifactSet.language`. |
| `target_name` | `str` | Defaults to `"default_fuzzer"` (FR-013); user-overridable. |
| `target_path` | `str` | Defaults to `f"/src/harness_source/default_fuzzer.{ext}"`, `ext` chosen from `language` (FR-013/FR-014); user-overridable. |
| `functions` | `list[BenchmarkFunction]` | Only entries from `FeatureArtifactSet.functions` where `is_public_api` is `True` (FR-012). `BenchmarkFunction` is `{name, signature, return_type, params}`, mirroring `FunctionSignature` minus `is_public_api`/`header_path`. |

**Relationships**: `BenchmarkYaml` is derived entirely from a
`FeatureArtifactSet` plus optional user overrides for `target_name` /
`target_path` — it has no independent state and is never constructed
directly from the native tool's output.

## State transitions

None of these entities are mutated after construction — each run of
`extract-features` produces a fresh `FeatureArtifactSet` written to disk
(overwriting any prior JSON, per FR-015), and each run of
`generate-benchmark` reads that JSON fresh and produces a fresh
`BenchmarkYaml` (overwriting any prior YAML, per FR-015). There is no partial
update or merge behavior, unlike `library_builder`'s accumulating
`_ProjectState`.
