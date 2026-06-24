# Library Builder oss-fuzz Implementation Plan

## Summary

This plan covers the `library_builder` feature of HarnessBuddy: a Python CLI
workflow that generates oss-fuzz project scaffolding for C/C++ libraries. The
first supported workflow is:

1. Accept a repository URL.
2. Clone it into temporary HarnessBuddy state.
3. Analyze C/C++ build-system signals deterministically.
4. Generate an oss-fuzz project with a Dockerfile and build scripts.
5. Compile every harness source under `/src/harness_source`.
6. Validate the generated project with oss-fuzz.
7. Use focused Codex/Claude subprocess agents only as fallback when deterministic
   generation or validation fails.

The generated oss-fuzz project should contain:

- `project.yaml`
- `Dockerfile`
- `build.sh`
- `build_library.sh`
- `compile_harnesses.sh`
- `harness_source/`
- `provenance.json`

## Locked Design Decisions

- Implement v1 as a Python CLI.
- Use `argparse` for the first CLI surface.
- Use a `core` package for shared infrastructure such as paths, repository
  handling, and subprocess helpers.
- Use a `library_builder` package for C/C++ library analysis, oss-fuzz
  generation, validation, and fallback agents.
- Target C and C++ library repositories first.
- Prefer deterministic build-system detection before any agent usage.
- Detect build systems in this order:
  1. CMake
  2. Meson
  3. Autotools/configure
  4. Makefile
  5. Existing Ninja
- Keep harness sources inside `/src/harness_source` in the oss-fuzz container.
- Use `compile_harnesses.sh` for multi-harness compilation.
- `build.sh` should only call `build_library.sh` and then
  `compile_harnesses.sh`.
- Host-side build exploration is opt-in through `--allow-host-build`.
- Normal validation should happen in isolated oss-fuzz/Docker flow.
- Agent fallback should use Auditron-style subprocess orchestration.

## Task 0: Initial Project Layout

Create the initial package layout before implementing feature behavior.

Requirements:

- Add `pyproject.toml` with:
  - Python 3.13
  - `uv_build`
  - console script `harnessbuddy`
  - development dependencies for `pytest`, `ruff`, and `ty`
- Add this source package structure:
  ```text
  src/harnessbuddy/
    __init__.py
    __main__.py
    cli.py
    core/
      __init__.py
      paths.py
      repos.py
      subprocesses.py
    library_builder/
      __init__.py
      models.py
      analysis.py
      generation.py
      validation.py
      agents.py
      templates/
        __init__.py
  ```
- Add this test structure:
  ```text
  tests/
    test_cli.py
    core/
      test_paths.py
      test_repos.py
    library_builder/
      test_analysis.py
      test_generation.py
      test_validation.py
    fixtures/
      repos/
      harnesses/
  ```
- Keep modules intentionally shallow in this task. Add placeholders only where
  needed for imports and CLI help.
- Define responsibilities:
  - `harnessbuddy.cli`: `argparse` entrypoint and command dispatch only.
  - `harnessbuddy.core.paths`: project/run-state paths, temporary directory
    helpers, and output path validation.
  - `harnessbuddy.core.repos`: repository URL/path normalization and future
    clone/copy helpers.
  - `harnessbuddy.core.subprocesses`: shared subprocess result types and safe
    command execution wrappers.
  - `harnessbuddy.library_builder.models`: dataclasses and enums for build
    systems, analysis results, generated outputs, and validation status.
  - `harnessbuddy.library_builder.analysis`: deterministic C/C++ build-system
    and header detection.
  - `harnessbuddy.library_builder.generation`: oss-fuzz project generation.
  - `harnessbuddy.library_builder.validation`: oss-fuzz helper integration.
  - `harnessbuddy.library_builder.agents`: Codex/Claude fallback orchestration
    for library-builder workflows.
  - `harnessbuddy.library_builder.templates`: generated script and Dockerfile
    template helpers.

Acceptance criteria:

- Package imports succeed.
- Shared package modules import without side effects.
- `uv run harnessbuddy --help` exits 0.
- `uv run harnessbuddy generate --help` exits 0.
- `uv run pytest -q` passes.
- `uv run ruff check` passes.
- `uv run ty check` passes.

## Task 1: Python CLI Scaffold

Create the initial Python package and command-line surface.

Requirements:

- Add `pyproject.toml`.
- Add a `src/harnessbuddy/` package.
- Add a console script named `harnessbuddy`.
- Implement:
  - `harnessbuddy --help`
  - `harnessbuddy generate --help`
- `generate` must parse:
  - positional `<repo-url>`
  - `--output <dir>`
  - `--project-name <name>`
  - `--skip-validation`
  - `--no-agents`
  - `--allow-host-build`
  - `--keep-workdir`
- The command may return a clear “not implemented” message after argument
  validation.
- Add a minimal test suite for CLI parsing.

Acceptance criteria:

- `uv run harnessbuddy --help` works.
- `uv run harnessbuddy generate --help` works.
- `uv run pytest -q` passes.
- `uv run ruff check` passes.
- `uv run ty check` passes.

## Task 2: Repository Input And Static Analysis

Implement repository ingestion and deterministic static analysis.

Requirements:

- Support repository URLs.
- Support local paths for tests.
- Clone or copy into temporary HarnessBuddy state.
- Infer project name from `--project-name` or the repository basename.
- Detect C/C++ build-system signals in priority order:
  - `CMakeLists.txt`
  - `meson.build`
  - `configure.ac`, `configure.in`, or `configure`
  - `Makefile` or `makefile`
  - `build.ninja`
- Detect public headers and likely language from file extensions.
- Do not execute target repository scripts.
- Return a structured analysis result containing:
  - project name
  - source location
  - selected build system
  - detected build files
  - detected headers
  - warnings

Acceptance criteria:

- Fixture tests cover each build-system type.
- Unsupported repositories fail with a clear diagnostic.
- No test requires network access.

## Task 3: oss-fuzz Project File Generation

Generate the first complete oss-fuzz project skeleton.

Requirements:

- Create an output directory named after the project.
- Generate:
  - `project.yaml`
  - `Dockerfile`
  - `build.sh`
  - `build_library.sh`
  - `compile_harnesses.sh`
  - `harness_source/default_fuzzer.cc`
  - `provenance.json`
- `Dockerfile` must copy all harness sources into `/src/harness_source`.
- All generated shell scripts must start with:
  ```bash
  #!/bin/bash
  set -euo pipefail
  ```
- `build.sh` must only orchestrate:
  ```bash
  "$SRC/build_library.sh"
  "$SRC/compile_harnesses.sh"
  ```
- `provenance.json` must record the selected build strategy and detected
  signals.

Acceptance criteria:

- Snapshot tests verify generated files for every fixture build-system type.
- Generated shell scripts are deterministic.

## Task 4: Deterministic Library Build Templates

Implement `build_library.sh` generation for supported C/C++ build systems.

Requirements:

- Build under `$WORK/harnessbuddy`.
- Prefer install prefix `$WORK/harnessbuddy/install`.
- Respect oss-fuzz compiler environment variables:
  - `$CC`
  - `$CXX`
  - `$CFLAGS`
  - `$CXXFLAGS`
- Generate build templates for:
  - CMake
  - Meson
  - Autotools/configure
  - Makefile
  - Existing Ninja
- Emit handoff files for harness compilation, such as:
  - include flags
  - library flags
  - static library paths
  - artifact notes
- Do not run host build commands unless `--allow-host-build` is provided.

Acceptance criteria:

- Generated scripts contain expected commands for each build-system fixture.
- Unit tests prove normal generation does not execute untrusted repository code.

## Task 5: Harness Source Contract

Implement the multi-harness source contract.

Requirements:

- Generate `harness_source/default_fuzzer.cc`.
- The default harness must define `LLVMFuzzerTestOneInput`.
- The default harness must not define `main`.
- `compile_harnesses.sh` must loop over direct files in
  `/src/harness_source`.
- Compile only:
  - `.c`
  - `.cc`
  - `.cpp`
  - `.cxx`
- Ignore:
  - headers
  - docs
  - corpora
  - nested directories
- Emit fuzzer binaries to `$OUT/<basename-without-extension>`.
- Use `$CC` for `.c`.
- Use `$CXX` for C++ sources.
- Link with `$LIB_FUZZING_ENGINE` and deterministic include/link flags.

Template shape:

```bash
#!/bin/bash
set -euo pipefail

HARNESS_DIR="/src/harness_source"

for harness in "$HARNESS_DIR"/*; do
  [ -f "$harness" ] || continue

  name="$(basename "$harness")"
  output="${name%.*}"

  case "$harness" in
    *.c)
      "$CC" $CFLAGS $HB_INCLUDE_FLAGS "$harness" \
        $HB_LIBRARY_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"
      ;;
    *.cc|*.cpp|*.cxx)
      "$CXX" $CXXFLAGS $HB_INCLUDE_FLAGS "$harness" \
        $HB_LIBRARY_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"
      ;;
  esac
done
```

Acceptance criteria:

- Tests prove source filtering.
- Tests prove output name derivation.
- Tests prove compiler selection by extension.

## Task 6: zlib Fixture And Gated Integration Test

Add the first real integration target.

Requirements:

- Add a local pretend zlib harness fixture, for example:
  `tests/fixtures/harnesses/zlib/zlib_crc_fuzzer.cc`.
- The harness should:
  - include `zlib.h`
  - define `LLVMFuzzerTestOneInput`
  - call a real zlib symbol such as `crc32`
- Add a gated integration test using:
  - repository: `https://github.com/madler/zlib.git`
  - tag: `v1.3.2`
- The integration test should:
  1. Generate the oss-fuzz project.
  2. Copy the zlib harness fixture into `harness_source/`.
  3. Run oss-fuzz validation when explicitly enabled.
- Skip Docker and network work unless `HARNESSBUDDY_RUN_DOCKER=1`.

Acceptance criteria:

- Normal tests do not require Docker or network access.
- The gated test validates the generated zlib project when enabled.

## Task 7: oss-fuzz Validation Driver

Implement validation through an oss-fuzz checkout.

Requirements:

- Auto-clone or update `google/oss-fuzz` into HarnessBuddy state when validation
  is enabled.
- Sync the generated project into:
  `oss-fuzz/projects/<project-name>`.
- Run:
  ```bash
  python3 infra/helper.py build_image <project-name>
  python3 infra/helper.py build_fuzzers --sanitizer address <project-name>
  python3 infra/helper.py check_build <project-name>
  ```
- Capture command stdout and stderr into the run directory.
- Return structured validation status containing:
  - command
  - exit code
  - stdout log path
  - stderr log path
  - summary status

Acceptance criteria:

- Mocked helper tests cover success.
- Mocked helper tests cover build failure.
- Mocked helper tests cover missing Docker.
- Mocked helper tests cover missing network.

## Task 8: Agent Fallback Orchestration

Add Auditron-style focused agent fallback.

Requirements:

- Launch real subprocess agents with `subprocess.Popen`.
- Non-interactive Codex agents run through `codex exec`.
- Non-interactive Claude agents run through `claude --print`.
- Store process records and logs in HarnessBuddy run state.
- Add at least two focused fallback tasks:
  - fix library build scripts after library build failure
  - fix harness compilation after compile failure
- Agents may edit generated files directly.
- HarnessBuddy must accept agent changes only after validation passes.
- `--no-agents` must prevent all agent launches.

Acceptance criteria:

- Fake `codex` and `claude` executables prove command construction.
- Tests prove logs and process records are written.
- Tests prove direct file edits are accepted only after validation passes.
- Tests prove `--no-agents` disables fallback.

## Task 9: End-To-End CLI Polish

Wire all stages into a usable v1 workflow.

Requirements:

- `harnessbuddy generate` should run:
  1. input handling
  2. static analysis
  3. project generation
  4. validation, unless skipped
  5. agent fallback, unless disabled
  6. final reporting
- Print a concise final report with:
  - output path
  - selected build system
  - validation status
  - generated fuzzers
  - warnings
  - log paths
- Make error messages clear and actionable for:
  - unsupported repository
  - clone failure
  - no C/C++ signals
  - validation failure
  - missing Docker
  - missing agent executable

Acceptance criteria:

- Full non-Docker test suite passes.
- Lint and type checks pass with zero warnings.
- Generated scripts are reviewed for unnecessary complexity.

## Deferred Work

- Real API-aware harness synthesis.
- Stronger proof that link paths exercise actual library code.
- UI or hosted workflow.
- Opening pull requests against oss-fuzz or upstream projects.
- Multi-language support beyond C/C++.
