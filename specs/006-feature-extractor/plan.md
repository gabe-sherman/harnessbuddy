# Implementation Plan: Library Feature Extraction for Fuzz Target Generation

**Branch**: `006-feature-extractor` | **Date**: 2026-07-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-feature-extractor/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Add a second HarnessBuddy tool, `feature_extractor`, that consumes a
`compile_commands.json` already produced by `library_builder` (or by wrapping
a non-CMake build with `bear`) and extracts the target library's function
signatures, typedefs, macros, enums, and struct/union definitions into a
single maximal JSON artifact, plus a curated YAML conversion matching
oss-fuzz-gen's benchmark input format. Per the spec's requester-confirmed
answers, both capabilities are exposed as `harnessbuddy` subcommands
(`extract-features`, `generate-benchmark`) and the YAML's default
`target_path` uses `harness_source/`, matching the directory name the
existing OSS-Fuzz generator already produces.

The extraction core is a native tool (`feature_extractor/native/`) built in
**C++ against Clang LibTooling** (`clang::tooling::ClangTool`,
`RecursiveASTVisitor`/`ASTMatchers`, `PPCallbacks`), per explicit requester
direction. LibTooling is a C++-only API with no stable ABI across LLVM
releases, so the tool is built and versioned as a C++ project rather than a
C one; that tradeoff is accepted knowingly (research.md §1) rather than
avoided. A thin Python package (`harnessbuddy.feature_extractor`)
builds/invokes that native binary, loads its JSON output into typed
dataclasses, and layers the YAML conversion (pure Python, using the
project's first runtime dependency, PyYAML) on top — mirroring the existing
`library_builder` split between deterministic analysis and artifact
generation.

## Technical Context

**Language/Version**: Python 3.13 (CLI integration, YAML conversion) +
C++17 (native extraction tool, matching the minimum standard modern
LLVM/Clang releases require), per the requester's explicit direction that
the extraction tool use Clang LibTooling and be C++-aligned.

**Primary Dependencies**: Clang **LibTooling** (`clangTooling`,
`clangASTMatchers`, `clangBasic`, `clangFrontend`, and the LLVM libraries
they depend on, located via `find_package(Clang)`/`find_package(LLVM)`) for
the native tool; CMake to build it; `PyYAML` (new — first runtime dependency
this project adds) for YAML emission. No new dependency for JSON — the
native tool emits JSON directly with a small dependency-free writer, and the
Python side uses the standard library `json` module to parse it.

**Storage**: Files only. Native tool output: one JSON file per project,
written into the shared per-project output directory `library_builder`
already creates (sibling of `local/` and `oss-fuzz/`, not duplicated inside
either). YAML conversion output: one YAML file in the same directory. The
compiled native binary is cached under the existing `.harnessbuddy/` state
directory (`core/paths.py`) so it is built once, not per invocation.

**Testing**: `pytest -q`, extending the existing single test runner. New
tests live under `tests/feature_extractor/`: integration tests build the
native binary once, run it as a real subprocess against the real zlib
checkout at repo-root `zlib_feature_test/` (already configured with
`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`, so it comes with a working
`compile_commands.json`), and assert on the resulting JSON/YAML content
against zlib's known public API — no mocking of the extraction logic itself,
no new C++-specific test framework. `zlib_feature_test/` is untracked
(`.gitignore`), the same convention as `output/` and
`ground_truth_test_output/`; it must exist locally (a real zlib clone built
with compile-commands export) for these tests to run.

**Target Platform**: Developer/CI host capable of building the target
library (Linux or macOS, matching `library_builder`'s existing requirement),
with LLVM/Clang development packages (headers plus the LibTooling static
libraries: `clangTooling`, `clangASTMatchers`, `clangBasic`, `clangFrontend`)
available in addition to the compilers `library_builder` already requires.

**Project Type**: Single project (Python CLI tool) with one embedded native
(C++) subcomponent — matches Option 1, extended with a `native/` subtree
inside the new tool package.

**Performance Goals**: N/A as a hard target — this is a one-shot, on-demand
AST walk over a single library's compilation database (tens to a few
thousand declarations), not a service under sustained load. Extraction
should complete in at most low minutes for any library `library_builder`
can already build, since it parses no more translation units than the
library's own build already compiled.

**Constraints**: Must not require `library_builder` or any part of the
pipeline to run twice — extraction reuses the `compile_commands.json` and
build artifacts already on disk. Must not silently drop declarations without
at least a warning-level signal (spec edge cases). Must not treat "no known
package mapping for a missing tool" the same as "malformed input" — a
missing `compile_commands.json` gets one specific, actionable error (FR-003).
Native tool build/runtime failures (e.g. LLVM/Clang development libraries not
installed, or a version mismatch between the LLVM/Clang the tool was built
against and what's available at run time — an accepted cost of LibTooling's
lack of a stable ABI, research.md §1) must surface as clearly as
`library_builder`'s existing missing-build-tool errors, not as an opaque
non-zero exit code.

**Scale/Scope**: One new top-level tool package
(`src/harnessbuddy/feature_extractor/`) plus its embedded native subtree, two
new CLI subcommands, one new runtime dependency (PyYAML). No changes to
`library_builder`'s existing behavior or output beyond it already being a
valid prerequisite (its CMake path already can pass
`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`; confirming/adding that flag if missing
is in scope as part of making this feature usable end-to-end).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Code Quality Is Non-Negotiable**: The Python glue
  (`models.py`, `native_build.py`, `extraction.py`, `benchmark_yaml.py`, and
  `cli.py` wiring) MUST pass `ruff format`, `ruff check`, `ty check`, and
  `pytest -q` with zero warnings exactly like existing code — same 100-line /
  complexity-8 / 5-param / absolute-import rules apply, no exception. The
  constitution's tooling (`ruff`/`ty`) doesn't cover the new C++ sources
  under `native/`, since they're not Python; this plan extends the same
  zero-silent-warnings philosophy to them via `-Wall -Wextra -Werror
  -Wpedantic -Wshadow` in `CMakeLists.txt` and a checked-in `clang-format`
  style (matching LLVM's own style, since the tool links directly against
  LLVM/Clang), so "PASS" is not a gap but an explicit, documented extension
  of Principle I's intent to the new language. **PASS**.
- **II. Modular Package Boundaries**: `feature_extractor` is a new
  self-contained top-level package under `src/harnessbuddy/` that depends on
  `core` (subprocess execution, path/state helpers) and never the reverse;
  `library_builder` is untouched. The native binary's stdout/JSON is loaded
  and validated into typed dataclasses (`models.py`) before anything else in
  the pipeline touches it — never passed around as a loose dict. **PASS**.
- **III. Extensible Multi-Tool Architecture**: This is exactly the second
  tool `plans/IDEAS.md` already anticipated (`artifact_extractor`). It
  registers its own subcommands through `harnessbuddy.cli` and reuses `core`
  primitives (`subprocesses.run_command_streaming`, `paths.default_state_dir`)
  rather than duplicating them, without modifying `library_builder`'s
  internals. **PASS**.
- **IV. Test-First, Behavior-Focused Testing**: Integration tests build and
  invoke the real native binary against the real zlib checkout at
  `zlib_feature_test/` (no mocking of the extraction/AST-walking logic,
  which is the whole point of the feature); the only things a test could
  reasonably mock — subprocess invocation, filesystem — are exercised for
  real, per the constitution's own rule that those aren't the boundaries to
  mock. No Docker or network required at test-run time (`zlib_feature_test/`
  is prepared once, locally, ahead of time, the same way
  `ground_truth_test_output/` is). **PASS**.
- **V. Simplicity and No Speculative Features**: No prebuilt/cross-compiled
  binary distribution, no plugin system, no schema versioning beyond a
  single `schema_version` field for future evolution, no support for
  generating compilation databases for build systems that don't already
  produce one (explicitly out of scope per spec Assumptions). The one new
  runtime dependency (PyYAML) is justified in `research.md` rather than
  added speculatively. **PASS**.
- **VI. Structured, Guardrailed Agent Invocation**: Not applicable — this
  feature invokes no LLM agent. **PASS / N/A**.

No violations to justify; Complexity Tracking table is empty.

## Project Structure

### Documentation (this feature)

```text
specs/006-feature-extractor/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/harnessbuddy/
├── cli.py                                  # register `extract-features` / `generate-benchmark` subcommands
├── core/                                   # unchanged; reused as-is
│   ├── paths.py                            #   state dir convention, reused to cache the compiled native binary
│   └── subprocesses.py                     #   streaming runner, reused to build/invoke the native binary
└── feature_extractor/
    ├── __init__.py
    ├── models.py                           # FeatureArtifactSet, FunctionSignature, Typedef, MacroDefinition,
    │                                        #   EnumDefinition, StructUnionDefinition, BenchmarkYaml dataclasses
    ├── native_build.py                     # locate/build the native binary once, cached under
    │                                        #   .harnessbuddy/native-build/ (core.subprocesses, core.paths)
    ├── extraction.py                       # invoke the built binary against compile_commands.json, parse +
    │                                        #   validate its JSON into FeatureArtifactSet, write JSON to the
    │                                        #   library's shared output directory
    ├── benchmark_yaml.py                   # FeatureArtifactSet -> BenchmarkYaml -> YAML (PyYAML), public-API
    │                                        #   filtering (FR-012), default target_name/target_path (FR-013/014)
    └── native/
        ├── CMakeLists.txt                  # C++17, -Wall -Wextra -Werror -Wpedantic -Wshadow,
        │                                    #   find_package(Clang)/find_package(LLVM), links LibTooling libs
        ├── include/
        │   └── feature_extractor.hpp
        └── src/
            ├── main.cpp                     # argv: compile_commands.json path, output JSON path; builds
            │                                #   the ClangTool and runs the FrontendAction below
            ├── extraction_action.cpp        # FrontendAction + RecursiveASTVisitor: functions/typedefs/enums/records
            ├── macro_callbacks.cpp          # PPCallbacks-based macro extraction
            └── json_writer.cpp              # minimal dependency-free JSON emitter for the maximal artifact

tests/
├── feature_extractor/
│   ├── conftest.py                          # points tests at repo-root zlib_feature_test/, skips the module
│   │                                         #   if that directory (or its compile_commands.json) is absent
│   ├── test_extraction.py                   # builds the native binary once per test session, runs it against
│   │                                         #   zlib_feature_test/, asserts on JSON content (deflate/inflate/
│   │                                         #   z_stream/ZEXTERN/etc. — zlib's real public API)
│   └── test_benchmark_yaml.py               # JSON -> YAML conversion: public-API filtering, defaults, overrides
└── test_cli.py                              # extend: new subcommands wired up, argument validation, error paths
                                              #   (missing-compile_commands.json case uses an empty tmp_path,
                                              #   not zlib_feature_test/)

zlib_feature_test/                           # real zlib clone + CMake build (-DCMAKE_EXPORT_COMPILE_COMMANDS=ON);
                                              #   untracked (.gitignore), same convention as output/ and
                                              #   ground_truth_test_output/; must exist locally to run these tests

pyproject.toml                               # add "pyyaml" to [project.dependencies]
```

**Structure Decision**: Single project (Option 1), extended with one new
top-level tool package (`feature_extractor/`) following the exact shape
Constitution Principle II/III already mandate for a second tool
(`models.py` for typed contracts, a deterministic-analysis-equivalent
module (`extraction.py`) separated from artifact generation
(`benchmark_yaml.py`)), plus an embedded native (`native/`) subtree that is
never imported directly by Python — only invoked as a built subprocess
binary, keeping the C/Python boundary at the JSON file contract.

## Post-Design Constitution Check

*Re-evaluated after Phase 1 (research.md, data-model.md, contracts/,
quickstart.md).*

Design did not change the Phase 0 assessment. `data-model.md` confirms the
JSON/YAML contracts are additive, typed structures with no cross-tool
coupling into `library_builder`; `contracts/` documents the native binary's
CLI surface and both file formats as the only integration points, which
keeps `feature_extractor` self-contained per Principle II. The LibTooling
decision recorded in `research.md` reflects the requester's explicit,
final direction (C++-aligned tool, real LibTooling) without introducing
speculative multi-backend abstraction — LibTooling is the single, final
choice, and its ABI-stability tradeoff is documented rather than papered
over. All six principles remain **PASS** for the reasons stated above. No
new violations to record in Complexity Tracking.

## Complexity Tracking

> No Constitution Check violations — this section is intentionally empty.
