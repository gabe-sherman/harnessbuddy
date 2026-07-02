# Feature Specification: Library Feature Extraction for Fuzz Target Generation

**Feature Branch**: `006-feature-extractor`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "I want to add a new feature to harnessbuddy to extract library artifacts like function signatures, typedefs, macros, enums, etc. This should be in a different subdirectory than library_builder (like feature_extractor). But it will depend on a compile_commands.json file as a result of -DCMAKE_EXPORT_COMPILE_COMMANDS or bear -- make. I think the user can run library_builder to get these artifacts, and then use feature_extractor next to get artifacts if they want. This should be a C tool that uses clang libtooling to extract these artifacts and store them in a json file in the libaries output directory. Furthermore, we should be able to extract these artifacts as a yaml file that aligns with the structure of oss-fuzz-gen's input (see example yaml file at ~/projects/oss-fuzz-gen/benchmark-sets/c-specific/croaring.yaml and its class at ~/projects/oss-fuzz-gen/experiment/benchmark.py). Target_name should be default_fuzzer, and target_path should be /src/harness_src/default_fuzzer.{ext}. The json output from feature_extractor should contain as much library information as possible, and then the yaml convertor will just be a tool that builds on top of it and uses its information"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Extract a library's API surface into a structured artifact file (Priority: P1)

A HarnessBuddy user has already run library_builder against a library, producing built artifacts and a compilation database (`compile_commands.json`). The user now wants a complete inventory of that library's API surface — function signatures, typedefs, macros, enums, structs/unions, and related declarations — without manually reading headers or writing an AST-parsing script themselves.

The user runs the feature extraction step against the library's output, pointing it at the compilation database, and receives a single structured file describing everything the tool could discover about the library's declarations.

**Why this priority**: This is the foundational capability — every other capability in this feature (YAML conversion for fuzz-target generation, future tooling) depends on this extracted artifact existing and being as complete as possible. Without it, there is nothing to build on.

**Independent Test**: Run library_builder against a CMake-based library to produce a `compile_commands.json`, then run feature extraction against that output. Confirm a JSON file is produced in the library's output directory containing function signatures, typedefs, macros, and enums that match what a manual header review finds.

**Acceptance Scenarios**:

1. **Given** a library output directory that already contains a valid `compile_commands.json` produced by library_builder, **When** the user runs feature extraction against it, **Then** a JSON file is written into that library's output directory containing the library's extracted functions, typedefs, macros, and enums.
2. **Given** a library with public functions declared across multiple headers, **When** feature extraction runs, **Then** every function's name, return type, ordered parameter types, and full textual signature appear in the JSON output.
3. **Given** a library that defines typedefs, macros, and enums in its public headers, **When** feature extraction runs, **Then** each is present in the JSON output with its defining details (typedef's underlying type, macro's parameters/value, enum's enumerators).
4. **Given** a library output directory with no `compile_commands.json` present, **When** the user runs feature extraction, **Then** the tool reports a clear, actionable error identifying the missing file and how to produce one, rather than failing silently or crashing.

---

### User Story 2 - Convert extracted artifacts into an oss-fuzz-gen-compatible benchmark file (Priority: P2)

A user who has already produced a library's JSON feature artifact (User Story 1) wants to hand that library off to oss-fuzz-gen for automated fuzz target generation. oss-fuzz-gen expects a specific YAML benchmark format naming the project, language, target build path/name, and a list of candidate functions with their signatures.

The user runs a conversion step against the JSON artifact and receives a YAML file in oss-fuzz-gen's expected benchmark format, ready to use as input to that tool without hand-editing.

**Why this priority**: This is the payoff for the extraction work in User Story 1 — it's what makes the extracted data immediately actionable for downstream fuzz target generation. It is sequenced after P1 because it consumes the JSON artifact rather than the library itself.

**Independent Test**: Given a JSON feature artifact produced by User Story 1, run the YAML conversion step and confirm the output file has the keys and structure oss-fuzz-gen's benchmark loader expects (`project`, `language`, `target_path`, `target_name`, `functions` with `name`/`signature`/`return_type`/`params`), and that oss-fuzz-gen can load the file without modification.

**Acceptance Scenarios**:

1. **Given** a JSON feature artifact for a library, **When** the user runs the YAML conversion step, **Then** a YAML file is produced containing `project`, `language`, `target_name`, `target_path`, and a `functions` list.
2. **Given** the same JSON feature artifact, **When** the YAML is generated, **Then** `target_name` defaults to `default_fuzzer` and `target_path` defaults to the library's designated harness build path for a `default_fuzzer` harness, unless the user supplies an override.
3. **Given** a JSON feature artifact containing internal/non-public declarations alongside public API functions, **When** the YAML is generated, **Then** the `functions` list contains only functions that are part of the library's externally-callable public API.

---

### User Story 3 - Feature extraction runs as an independent, later pipeline step (Priority: P3)

A user wants to treat library building and feature extraction as two separate, sequential decisions: build the library first (via library_builder), inspect the result, and only then decide whether they also want the extracted feature artifacts. They should not be forced to extract features every time they build a library, and extraction should not require re-running or duplicating any part of the build.

**Why this priority**: This is a workflow/usability guarantee once P1 and P2 exist — it ensures the feature composes cleanly with the existing library_builder pipeline instead of being bolted on or forcing unwanted work.

**Independent Test**: Run library_builder alone and confirm no feature-extraction artifacts are produced. Then, on that same output directory, separately invoke feature extraction and confirm it succeeds using only the artifacts library_builder already produced, without re-invoking any library build step.

**Acceptance Scenarios**:

1. **Given** a library output directory produced by library_builder, **When** the user does not run feature extraction, **Then** no feature-extraction JSON or YAML files are created and the library_builder output is otherwise unaffected.
2. **Given** the same output directory, **When** the user later runs feature extraction against it, **Then** the tool reuses the existing build artifacts and compilation database without rebuilding the library.

---

### Edge Cases

- `compile_commands.json` is missing from the library's output directory: the tool reports which file is missing and how to produce it (e.g. via CMake's compile-commands export or `bear`) rather than crashing.
- `compile_commands.json` exists but references source files that no longer exist on disk (a stale database): the tool skips those entries with a warning instead of aborting the entire run.
- `compile_commands.json` was produced for a build system other than CMake (e.g. a Makefile project run through `bear -- make`): extraction behaves identically regardless of how the database was produced.
- A header declares a function, typedef, macro, or enum but no compiled translation unit in the database ever includes that header: that declaration is invisible to extraction (an inherent limitation of relying on a compilation database), and this is treated as expected behavior, not an error.
- The same declaration is visible from more than one translation unit (a shared header included by multiple source files): it appears exactly once in the JSON output, not once per translation unit.
- A macro is defined conditionally behind a preprocessor guard that isn't enabled for any compiled translation unit in the database: it is not extracted, consistent with only seeing what the compiler itself saw.
- The library exposes no public functions at all (e.g. a header-only constants library): extraction still succeeds and produces a JSON file whose `functions` collection is empty rather than failing.
- Running feature extraction a second time against the same output directory: the JSON (and, if requested, YAML) files are overwritten with the latest extraction rather than accumulating stale duplicates.
- Running feature extraction against an output directory that library_builder did not produce (unexpected layout, no recognizable build artifacts): the tool reports a clear error rather than producing a partial or misleading result.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a feature-extraction capability that a user can invoke as a distinct step after library_builder has already produced a library's output directory, without re-running any part of the library build.
- **FR-002**: Feature extraction MUST consume an existing `compile_commands.json` compilation database as its source of truth for what to analyze, regardless of whether that database was produced via CMake's compile-commands export or a tool such as `bear` wrapping a non-CMake build.
- **FR-003**: When the expected `compile_commands.json` is missing or unreadable, the system MUST report a clear, actionable error identifying the missing file and how a user can produce one, instead of crashing or silently producing an empty result.
- **FR-004**: System MUST extract, for every function declaration visible in the compilation database, its name, return type, ordered parameter list (type and, where available, name), and full textual signature.
- **FR-005**: System MUST extract every typedef visible in the compilation database, including its name and underlying/aliased type.
- **FR-006**: System MUST extract every macro definition visible in the compilation database, including its name, whether it is function-like or object-like, its parameters (if function-like), and its defined value/replacement text.
- **FR-007**: System MUST extract every enum definition visible in the compilation database, including its name (if any) and its enumerators' names and values.
- **FR-008**: System MUST extract struct and union definitions visible in the compilation database, including their name (tag), and field names and types.
- **FR-009**: System MUST tag or otherwise distinguish declarations that are part of the library's externally-callable public API (declared in the library's own headers, with external linkage) from internal/private declarations (static functions, declarations local to `.c`/`.cpp` translation units), so that downstream consumers can filter appropriately.
- **FR-010**: System MUST write the complete set of extracted declarations for a library to a single JSON file within that library's existing output directory (the same top-level per-project directory library_builder already produces, not duplicated separately inside each generated project format).
- **FR-011**: System MUST provide a conversion capability, layered on top of the JSON artifact from FR-010, that produces a YAML file matching oss-fuzz-gen's benchmark input structure: `project`, `language`, `target_path`, `target_name`, and a `functions` list where each entry has `name`, `signature`, `return_type`, and `params`.
- **FR-012**: The YAML conversion MUST include, in its `functions` list, only functions identified as part of the library's externally-callable public API (per FR-009) — internal/private declarations MUST be excluded from the YAML even though they may be present in the JSON.
- **FR-013**: The YAML conversion MUST default `target_name` to `default_fuzzer` and `target_path` to the library's designated harness build path for that fuzzer, using the extension appropriate to the library's language (e.g. `.c` or `.cc`), unless the user supplies an override.
- **FR-014**: System MUST use `harness_source` as the directory component of the default `target_path`, matching the directory name already used by the generated OSS-Fuzz project layout (`/src/harness_source/default_fuzzer.{ext}`).
- **FR-015**: Running feature extraction more than once against the same library output directory MUST overwrite the previous JSON/YAML output rather than accumulating multiple stale copies.
- **FR-016**: System MUST expose feature extraction and YAML conversion as subcommands of the existing `harnessbuddy` command-line tool, so a user does not need to separately locate, build, or invoke a different executable by name.

### Key Entities

- **Feature Artifact Set (JSON)**: The complete collection of declarations extracted for one library — functions, typedefs, macros, enums, structs/unions — plus enough metadata (project name, language) to support conversion to other formats.
- **Function Signature**: A single function's name, return type, ordered parameters (type and name), full textual signature, and public/internal classification.
- **Typedef**: A name and its underlying/aliased type.
- **Macro Definition**: A name, function-like/object-like classification, parameter list (if applicable), and defined value.
- **Enum Definition**: A name (if present) and its list of enumerator name/value pairs.
- **Struct/Union Definition**: A tag name and its field list (name and type per field).
- **Benchmark YAML (oss-fuzz-gen input)**: A derived, per-project file containing `project`, `language`, `target_name`, `target_path`, and the subset of extracted functions that are part of the library's public API, structured to match oss-fuzz-gen's benchmark loader.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any library with a valid `compile_commands.json`, feature extraction produces a JSON artifact containing the library's function signatures, typedefs, macros, and enums without requiring the user to hand-edit or supplement the result.
- **SC-002**: A YAML file produced by the conversion step can be loaded by oss-fuzz-gen's benchmark loader without any manual modification, for 100% of libraries that successfully complete extraction.
- **SC-003**: A user can go from a library output directory already produced by library_builder to a populated JSON feature artifact, and from there to an oss-fuzz-gen-ready YAML file, using two sequential commands with no manual data entry.
- **SC-004**: Every run against a missing or invalid `compile_commands.json` produces a specific, actionable error message rather than an unhandled crash or a silently empty/misleading result.
- **SC-005**: Re-running feature extraction against the same library output directory never leaves behind more than one JSON and one YAML artifact for that library.

## Assumptions

- library_builder (or an equivalent build step performed outside HarnessBuddy) is responsible for making a valid `compile_commands.json` available before feature extraction runs. For CMake-based libraries this is a low-cost addition to the existing build step; for non-CMake build systems, the user is responsible for producing the compilation database themselves (e.g. via `bear -- make`). Guaranteeing an automatically generated compilation database for every non-CMake build system is out of scope for this feature.
- "The library's own headers" (used to distinguish public API from internal declarations, and to scope extraction away from system/third-party headers) is determined using the same source/header location information library_builder already records for a project.
- Both C and C++ libraries are supported for extraction, since the underlying analysis technique (Clang LibTooling, per the requester's direction) parses both language families; the extraction tool itself being implemented in C++ (LibTooling's native language) is an implementation choice and does not restrict which target libraries can be analyzed.
- The JSON artifact is intentionally maximal — it captures both public and internal declarations the compiler saw — while the YAML conversion step is intentionally curated, narrowing that same data down to the externally-callable public API that oss-fuzz-gen needs for fuzz target generation.
- The JSON artifact is written once per project into the shared top-level output directory library_builder already creates for that project (the common parent of its `local/` and `oss-fuzz/` outputs), since the artifact is format-agnostic and not specific to either generated project layout.
- YAML conversion is a separate, on-demand step rather than something that runs automatically every time JSON extraction runs, consistent with it being described as a tool that builds on top of the JSON rather than a mandatory part of extraction.
- Declarations that are never reachable from any translation unit listed in the compilation database (e.g. code behind a preprocessor guard that's never enabled, or a header no compiled file includes) are not extracted; this is treated as an inherent limitation of compilation-database-driven analysis, not a defect.
