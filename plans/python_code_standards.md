# Python Codebase Standards

## Purpose

These standards apply to Python code added for HarnessBuddy, starting with the
`library_builder` CLI workflow described in `library_builder_oss_fuzz.md`.

The project is still at the scaffolding stage, so prefer explicit, boring code
over broad abstractions. Add conventions only when they help implement the
planned workflow: ingest a C/C++ repository, analyze it deterministically,
generate oss-fuzz project files, validate them, and report clear results.

## Runtime And Tooling

- Use Python 3.13.
- Manage the environment and dependencies with `uv`.
- Use the `src/` package layout:
  - `src/harnessbuddy/`
  - `tests/`
- Keep runtime dependencies minimal. Add a dependency only when a concrete
  workflow needs it and the standard library is a poor fit.
- Pin dependency versions when dependencies are introduced.

Required quality commands:

```bash
uv run ruff format
uv run ruff check
uv run ty check
uv run pytest -q
```

The baseline is zero warnings from linting, formatting, type checking, and
tests. If a warning cannot be removed, use the narrowest inline ignore and
include a short justification.

## Formatting Standards

- Format Python with `ruff format`.
- Keep lines at or below 100 characters.
- Use absolute imports only.
- Keep imports sorted by `ruff`.
- Prefer double quotes for strings, matching `ruff format` defaults.
- Avoid commented-out code. Delete obsolete code instead.
- Write comments only when they explain non-obvious intent, safety constraints,
  or external tool behavior.

## Linting Standards

Use `ruff check` as the project linter. The first `pyproject.toml` should enable
at least:

- `E` and `F` for pycodestyle and pyflakes correctness checks.
- `I` for import sorting.
- `B` for likely bug patterns.
- `UP` for modern Python syntax.
- `ARG` for unused arguments.
- `SIM` for avoidable complexity.
- `C4` for comprehensions.
- `DTZ` if date or time handling is added.
- `RUF` for Ruff-specific checks.

Do not add broad per-file ignores. Prefer small code changes that make the
warning disappear.

## Type Checking Standards

Use `ty check` for static typing. New Python modules should be type-checkable
from the start.

- Add annotations to public functions, methods, dataclasses, and command
  dispatch boundaries.
- Use `pathlib.Path` instead of raw string paths.
- Use dataclasses or enums for structured workflow state.
- Avoid untyped dictionaries for cross-module contracts.
- Return structured results for analysis, generation, validation, and agent
  fallback status instead of loose tuples.
- Avoid `Any`. If it is unavoidable at an external boundary, narrow it as soon
  as possible.

## Package Design

Keep module ownership aligned with the implementation plan:

- `harnessbuddy.cli`: `argparse` entrypoint and command dispatch only.
- `harnessbuddy.core.paths`: run-state paths, temporary directory helpers, and
  output path validation.
- `harnessbuddy.core.repos`: repository URL/path normalization and clone/copy
  helpers.
- `harnessbuddy.core.subprocesses`: subprocess result types and safe command
  execution wrappers.
- `harnessbuddy.library_builder.models`: dataclasses and enums for build
  systems, analysis results, generated outputs, and validation status.
- `harnessbuddy.library_builder.analysis`: deterministic C/C++ build-system and
  header detection.
- `harnessbuddy.library_builder.generation`: oss-fuzz project generation.
- `harnessbuddy.library_builder.validation`: oss-fuzz helper integration.
- `harnessbuddy.library_builder.agents`: focused Codex/Claude fallback
  orchestration.

Modules should import without side effects. Importing `harnessbuddy` must not
clone repositories, read global config, create directories, start containers, or
launch subprocesses.

## Coding Practices

- Keep functions small and behavior-focused.
- Keep public functions at or below five positional parameters.
- Prefer keyword-only parameters when a call would otherwise be ambiguous.
- Prefer clear conditionals over clever comprehensions when handling workflow
  decisions or error paths.
- Use enums for build-system and validation states rather than stringly typed
  sentinels.
- Keep deterministic analysis separate from file generation.
- Preserve provenance for generated files. Record which detected signals led to
  each generated build decision.
- Do not document or validate behavior that has not been implemented.
- Replace obsolete code completely when a new implementation supersedes it.

## CLI Standards

- Use `argparse` for the initial CLI.
- Keep parsing and dispatch in `harnessbuddy.cli`; put workflow logic in
  library modules.
- Error messages should say:
  - what operation failed,
  - which input caused it,
  - what the user can do next.
- `--output <dir>` is a parent directory. The generated project path is
  `<dir>/<project-name>`.
- Do not silently overwrite existing generated project directories.
- Permission flags, such as `--allow-host-build`, must not execute extra work
  unless that behavior is explicitly implemented and tested.

## Repository Safety

Target repositories are untrusted input.

- Do not execute target repository scripts during deterministic analysis.
- Do not run host builds in v1. `--allow-host-build` only records permission for
  future workflows.
- Prefer static file detection for build-system signals.
- Use tiny local repository fixtures for normal tests.
- Gate Docker and network validation behind explicit opt-in, such as
  `HARNESSBUDDY_RUN_DOCKER=1`.
- Treat missing Docker or missing network as actionable validation failures, not
  as reasons to launch fallback agents.

## Subprocess Standards

- Use shared subprocess helpers instead of ad hoc `subprocess.run` calls across
  the codebase.
- Pass commands as argument lists, not shell strings.
- Avoid `shell=True`.
- Capture stdout, stderr, command, exit code, and log paths in structured
  results.
- Include enough context in failures to reproduce the command.
- Never pass untrusted repository input through a shell.

## Generated File Standards

Generated oss-fuzz files should be deterministic and readable.

- Generated shell scripts must start with:

  ```bash
  #!/bin/bash
  set -euo pipefail
  ```

- `build.sh` should only orchestrate `build_library.sh` and
  `compile_harnesses.sh`.
- `provenance.json` should record selected build strategy and detected signals.
- Snapshot tests should cover generated files for each supported build-system
  fixture.
- Do not include contact metadata or other project facts that HarnessBuddy does
  not know deterministically.

## Testing Standards

Use `pytest` for tests and keep test structure close to the package structure.

- Test behavior, not implementation details.
- Cover malformed inputs, unsupported repositories, missing origins, and output
  directory conflicts.
- Add fixture tests for each supported build-system signal:
  - CMake
  - Meson
  - Autotools/configure
  - Makefile
  - Existing Ninja
- Mock boundaries such as subprocesses, Docker, network access, and filesystem
  failures.
- Do not mock the deterministic analysis logic itself.
- Normal tests must not require Docker or network access.
- Add gated integration tests only when they are explicitly skipped by default.

## Documentation Standards

- Document implemented behavior only.
- Keep user-facing examples runnable once the referenced task is implemented.
- Prefer concise module docstrings for non-obvious module responsibilities.
- Use Google-style docstrings for non-trivial public APIs.
- When a command can fail for environmental reasons, document the expected
  failure mode and the action the user can take.

## Pre-Commit Checklist

Before committing Python changes:

1. Re-read the diff for unnecessary abstraction, unclear naming, and duplicated
   workflow logic.
2. Run focused tests for the touched behavior.
3. Run:

   ```bash
   uv run ruff format
   uv run ruff check
   uv run ty check
   uv run pytest -q
   ```

4. Fix every warning or add a narrow inline ignore with justification.
5. Confirm generated fixtures or snapshots changed only where intended.
