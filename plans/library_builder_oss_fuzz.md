# Library Builder oss-fuzz Implementation Plan

## Summary

This plan covers the `library_builder` feature of HarnessBuddy: a Python CLI
workflow that generates oss-fuzz project scaffolding for C/C++ libraries. The
first supported workflow is:

1. Accept a repository URL.
2. Clone or copy it into repo-local HarnessBuddy state.
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
- Host-side build exploration is not implemented in v1. `--allow-host-build`
  only records permission for future workflows.
- Normal validation should happen in isolated oss-fuzz/Docker flow.
- Agent fallback should use Auditron-style subprocess orchestration.

## Clarified Implementation Decisions

- Task 0 creates the initial package layout and basic CLI help. Task 1 owns the
  full `generate` argument surface and parsing tests.
- `generate` accepts `--repo-ref <ref>` for optional branch, tag, or commit
  checkout after Dockerfile cloning.
- `generate` accepts `--agent auto|codex|claude`. `auto` is the default.
  `--no-agents` still disables all fallback launches.
- `--output <dir>` is a parent directory. HarnessBuddy writes
  `<dir>/<project-name>` and fails if that project directory already exists.
- HarnessBuddy stores run state, cloned repositories, logs, and oss-fuzz
  checkouts in repo-local `.harnessbuddy/` by default.
- Generated Dockerfiles clone the original repository URL and check out
  `--repo-ref` when it is provided.
- Local path inputs are supported for analysis. For generated Dockerfiles,
  HarnessBuddy uses the local repository's git `origin` URL and fails clearly
  when no cloneable origin is available.
- `build_library.sh` writes `$WORK/harnessbuddy/build.env` with
  `HB_INCLUDE_FLAGS` and `HB_LIBRARY_FLAGS`. `compile_harnesses.sh` sources that
  file before compiling harnesses.
- Missing Docker or missing network is a validation failure with an actionable
  message and does not trigger agent fallback.
- `--allow-host-build` is permission-only in v1. It is parsed and carried
  through the workflow, but no host build exploration is implemented yet.
- Agent fallback should copy only the focused subprocess pieces from Auditron,
  not its long-running team scheduler, dashboard, workflow database, or
  per-agent git worktree model.
- Agent fallback prompts live in package-local templates. Existing files under
  `prompts/` are for external issue orchestration, not runtime fallback.
- Failed agent edits are left in the generated project for inspection, with
  logs and validation status explaining that the output failed validation.
- `project.yaml` omits contact metadata in v1.
- For `.c` harness files, `compile_harnesses.sh` uses `$CC` for the full
  compile/link command, matching this plan even though OSS-Fuzz documentation
  recommends `$CXX` for final fuzz target linking.

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
  - `--repo-ref <ref>`
  - `--skip-validation`
  - `--no-agents`
  - `--agent auto|codex|claude`
  - `--allow-host-build`
  - `--keep-workdir`
- `--output <dir>` must be treated as a parent directory for
  `<dir>/<project-name>`.
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
- Clone or copy into repo-local `.harnessbuddy/` run state.
- Infer project name from `--project-name` or the repository basename.
- For local path inputs, use the local repository's git `origin` URL for
  generated Dockerfiles and fail clearly if no cloneable origin is available.
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
- `Dockerfile` must clone the repository URL into `$SRC/<project-name>` and
  check out `--repo-ref` when one is provided.
- `project.yaml` should include only metadata HarnessBuddy can know
  deterministically in v1 and must omit contact metadata.
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
- The initial handoff contract is `$WORK/harnessbuddy/build.env`, containing
  shell assignments for `HB_INCLUDE_FLAGS` and `HB_LIBRARY_FLAGS`.
- Do not run host build commands in v1. `--allow-host-build` only records
  permission for future workflows.

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
- For `.c` harness files, use `$CC` for the full compile/link command.

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
- The generated zlib project should use `--repo-ref v1.3.2`.
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
- Missing Docker and missing network should return actionable validation
  failures and should not trigger agent fallback.

Acceptance criteria:

- Mocked helper tests cover success.
- Mocked helper tests cover build failure.
- Mocked helper tests cover missing Docker.
- Mocked helper tests cover missing network.

## Task 8: Agent Fallback Orchestration

Add Auditron-style focused agent fallback.

Requirements:

- Model the fallback after Auditron's subprocess primitives:
  - an explicit agent spec containing tool, optional model, and reasoning effort
  - a focused prompt builder for one assigned repair task
  - a tool-specific non-interactive command builder
  - a `subprocess.Popen` launch with `stdin=subprocess.DEVNULL`
  - stdout/stderr capture to a run-state log file
  - a JSON process record containing handle, role, pid, command, cwd, log path,
    start/update timestamps, exit code, and runtime status
  - timeout handling that terminates the child process and marks the run failed
- Do not implement Auditron's long-running operator, scheduler, dashboard,
  SQLite workflow store, heartbeats, inbox nudging, or per-agent git worktrees.
- HarnessBuddy runs one short-lived fallback subprocess per repair attempt and
  waits for it to exit before re-running validation.
- Non-interactive Codex agents run through `codex exec`.
- Non-interactive Claude agents run through `claude --print`.
- The CLI chooses fallback behavior with `--agent auto|codex|claude`, defaulting
  to `auto`.
- Store focused fallback prompt templates in the library-builder package, not in
  the repo-level `prompts/` directory.
- Store process records and logs in HarnessBuddy run state.
- Use stable repair handles such as `build-script-fixer` and
  `harness-compile-fixer` so logs and JSON records are predictable.
- Add at least two focused fallback tasks:
  - fix library build scripts after library build failure
  - fix harness compilation after compile failure
- Agents may edit generated files directly.
- Fallback prompts must include:
  - the project output path
  - the repository URL and optional repo ref
  - the selected build system
  - relevant validation command and log excerpts
  - the exact files the agent is allowed to edit
  - instructions to make the smallest build-script or harness-compile repair
    and not touch unrelated files
- HarnessBuddy must report whether validation passed after agent edits. Failed
  edits are left in place for inspection with logs and validation status.
- `--no-agents` must prevent all agent launches.

Acceptance criteria:

- Fake `codex` and `claude` executables prove command construction.
- Tests prove logs and process records are written.
- Tests prove subprocess stdout and stderr are captured in the configured log
  path.
- Tests prove timeout handling marks the fallback attempt failed.
- Tests prove fallback prompts include the failing command, log excerpt, allowed
  files, and selected build system.
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
  - run-state path
  - selected repository ref, when provided
  - selected agent mode
- Reports should call out failed agent edits left in place and validation
  failures caused by missing Docker or network.
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
