# Phase 0 Research: Library Feature Extraction for Fuzz Target Generation

## 1. Clang API choice: Clang LibTooling, tool implemented in C++

**Decision**: Build the native extraction tool against **Clang LibTooling**
(`clang::tooling::ClangTool`, `RecursiveASTVisitor`/`ASTMatchers`,
`FrontendAction`), implementing the tool itself in **C++**, per explicit
requester direction overriding the initial libclang-based proposal from this
plan's first draft.

**Rationale**: LibTooling is a C++-only API — there is no way to call it
from a tool implemented in C, so honoring "uses clang libtooling" requires
the tool to be C++. LibTooling gives direct access to the full Clang AST
(`clang::FunctionDecl`, `TypedefDecl`, `EnumDecl`, `RecordDecl`) with richer
traversal and matching (`ASTMatchers`) than libclang's cursor-based C API,
including precise linkage information
(`clang::NamedDecl::getFormalLinkage()`) for the public/internal
classification in FR-009, and macro visibility via `PPCallbacks` registered
on the `Preprocessor` for FR-006.

**Accepted tradeoff**: LibTooling has no stable ABI across LLVM/Clang
releases — the native tool must be built against a specific LLVM/Clang
version's headers and libraries (`clangTooling`, `clangASTMatchers`,
`clangBasic`, `clangFrontend`, plus the LLVM support libraries they depend
on), and rebuilt whenever that version changes. This is a real, knowingly
accepted cost of the requester's explicit choice, not something this plan
tries to hide or work around; `research.md` §2 documents the build/link
consequences.

**Alternatives considered**:
- *libclang's C API* (this plan's original proposal): would keep the tool
  in C and avoid the ABI-stability cost above, but the requester explicitly
  asked for LibTooling and a C++-aligned tool instead, so this alternative
  is no longer pursued.
- *Shell out to `clang -Xclang -ast-dump=json` and parse Clang's AST-dump
  JSON*: rejected — Clang explicitly documents `-ast-dump` as a debugging
  aid with no format stability guarantee across versions, an unsound
  foundation for a durable artifact contract other tooling (oss-fuzz-gen)
  will depend on.
- *A pure-Python or third-party C/C++ parser (e.g. `pycparser`) instead of
  Clang*: rejected — doesn't consume the project's real compiler flags,
  target-specific typedefs, or macro state the way driving the actual
  compilation database through Clang does, and has no meaningful C++
  support (the spec assumes both C and C++ libraries are supported).

## 2. Native tool build orchestration

**Decision**: The Python `feature_extractor` package builds `native/` once
via CMake, invoked through the existing `core.subprocesses` streaming
runner, caching the compiled binary under the project's existing
`.harnessbuddy/` state directory convention (`core/paths.py`) so later runs
reuse it without rebuilding, similar to how `library_builder` already
assumes host build tools are present and drives them via the same
subprocess runner. `native/CMakeLists.txt` locates the host's LLVM/Clang
installation via the standard `find_package(Clang REQUIRED CONFIG)` /
`find_package(LLVM REQUIRED CONFIG)` mechanism and links the specific
LibTooling libraries the tool needs (`clangTooling`, `clangASTMatchers`,
`clangBasic`, `clangFrontend`). Because LibTooling has no stable ABI across
LLVM releases (research §1), the cached binary is keyed to the
LLVM/Clang version CMake found at build time; if that version changes, the
cache is invalidated and the tool is rebuilt rather than silently reused
against a mismatched runtime.

**Rationale**: Reuses existing, already-tested infrastructure
(`run_command_streaming`, the state-dir convention) instead of introducing a
second packaging mechanism. Building on first use — rather than requiring a
separate manual build step before `harnessbuddy` can be used — keeps the
"two sequential commands" UX promised by spec SC-003 and the subcommand
integration decided in FR-016.

**Alternatives considered**:
- *Prebuilt/cross-compiled binaries bundled per platform*: rejected as
  premature — adds cross-compilation and release-artifact complexity the
  project doesn't need yet (Principle V), and no other HarnessBuddy tool
  ships prebuilt binaries today.
- *Require users to build and install the native tool themselves first
  (separate README/build step)*: rejected — breaks the two-command
  end-to-end flow the spec commits to and reintroduces the "separately
  locate a different executable" friction FR-016 exists to remove.

## 3. YAML conversion: pure Python + PyYAML, not native code

**Decision**: Implement the JSON → oss-fuzz-gen-YAML conversion
(`benchmark_yaml.py`) in pure Python using **PyYAML** — the project's first
runtime dependency — rather than in the native tool or via a hand-rolled
serializer.

**Rationale**: YAML generation here is a narrow, format-only transformation
over data the native tool already extracted (filter to public API, rename
fields, wrap in the benchmark structure) — it never touches the AST, so
there's no reason for it to live in the native tool. Hand-rolling YAML
scalar quoting is a known correctness/security foot-gun for exactly the kind
of free-form text this feature serializes (function signatures and macro
values containing colons, quotes, or embedded characters); PyYAML is a
mature, widely-used library that handles that correctly. `oss-fuzz-gen`
itself loads benchmarks with `yaml.safe_load`, so only structural/content
correctness matters, not byte-for-byte formatting parity with
`Benchmark.to_yaml`'s specific output style.

**Alternatives considered**:
- *Hand-rolled minimal YAML emitter*: rejected — real correctness risk on
  scalar escaping for no real benefit, given the project already accepts
  narrowly-scoped, justified runtime dependencies elsewhere when warranted.
- *Emit YAML from the native (C) tool*: rejected — would mean vendoring a C
  YAML library or reimplementing the same escaping problem in a lower-level
  language, for a step that never needs AST access.

## 4. JSON as the sole durable contract

**Decision**: The JSON artifact (FR-010) is the only contract between the
native tool and everything downstream — the Python YAML conversion, and any
future consumer. It carries a top-level `schema_version` field for informal
forward compatibility; no other versioning or negotiation mechanism is
introduced.

**Rationale**: Keeps the native tool's responsibility singular (parse +
emit the maximal artifact) and keeps all curation logic (public-API
filtering for YAML) in Python, where it's simpler to test and evolve without
recompiling a native binary. Directly required by FR-010/FR-011's split
between a maximal JSON and a curated YAML "built on top of it."

**Alternatives considered**: N/A — this follows directly from the spec's
own JSON/YAML separation; no other design satisfies FR-010 and FR-011
independently.

## 5. Public vs. internal declaration classification (FR-009)

**Decision**: Classify a declaration as part of the library's externally
callable public API when both hold: (a) `clang::NamedDecl::getFormalLinkage()`
reports external linkage, and (b) the declaration's location is inside a
header the library itself records as part of its public/installed headers
(reusing the same header/include-location information `library_builder`'s
analysis already captures for the project), as opposed to a `.c`/`.cpp`
translation unit or a third-party/system header.

**Rationale**: Linkage is a precise, compiler-verified signal — no naming
heuristics (e.g. leading underscore) are needed or reliable. Combining it
with the existing header-location filter (already assumed in the spec for
scoping extraction away from system headers) correctly excludes
`static inline` helpers that have external-looking declarations but aren't
meant as public API, while still including legitimately exported functions.

**Alternatives considered**:
- *Header location alone*: rejected — a public header can still declare
  `static`/`static inline` helpers not intended as public API.
- *Naming convention heuristics*: rejected — no existing precedent in this
  codebase, and unreliable across differently-styled libraries.

## 6. Testing strategy for the native tool

**Decision**: No new C++-specific test framework or `ctest` invocation path.
Integration tests under `tests/feature_extractor/` build the native binary
once per test session and run it as a real subprocess against the **real
zlib checkout at `zlib_feature_test/`** (repo root, alongside `output/` and
`ground_truth_test_output/` as an untracked, locally-prepared fixture — see
`.gitignore`), which already has a real `compile_commands.json` generated by
CMake (`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`). Tests assert on known parts of
zlib's real public API — e.g. the `deflate`/`inflate`/`deflateInit`/
`inflateInit` functions, the `z_stream`/`z_streamp` typedefs backed by the
`z_stream_s` struct, and `ZEXTERN`/`ZEXPORT` macros — rather than a
purpose-built synthetic fixture.

**Rationale**: Matches Constitution Principle IV directly — mock only
genuine external boundaries, and here the extraction logic itself *is* the
behavior under test, so it must run for real. Using a real, well-known
library instead of a synthetic stand-in exercises the tool against the same
kind of `compile_commands.json` and header structure it will see in
production, and the fixture already exists and comes with a working
`compile_commands.json`, so there's no separate fixture-authoring step to
maintain. Keeping everything under `pytest -q` avoids fragmenting the
project's single test-runner convention into a second (`ctest`) invocation
path that CI and local workflows would both need to know about.

**Alternatives considered**:
- *Small, purpose-built synthetic C/C++ fixture sources* (this plan's
  original proposal): rejected in favor of the already-available real zlib
  checkout, which needs no authoring and exercises real-world complexity
  (conditional macros, opaque struct typedefs) a minimal synthetic fixture
  wouldn't surface.
- *CTest-based C++ unit tests*: rejected for now — would add a second test
  runner the constitution's `pytest -q` gate doesn't cover. Revisit only if
  native-tool-internal logic grows complex enough that black-box JSON
  assertions stop giving adequate coverage of individual functions.

**Known gap**: `zlib_feature_test/` is a C library, so this fixture alone
does not exercise C++-specific extraction paths (e.g. namespaces, classes,
templates). Validating C++ target libraries is left for a follow-up fixture
rather than solved by this update.

## Outstanding NEEDS CLARIFICATION markers

None. All unknowns identified while filling Technical Context were resolved
above; the two scope-defining questions from `/speckit-specify` (invocation
model, default `target_path` directory name) were already resolved with the
requester before this plan was written and are reflected in the spec's
FR-014 and FR-016.
