# Task 0: Initial Project Layout

**Status**: completed

## Summary

Create the initial package layout before implementing feature behavior.

## Requirements

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

## Acceptance Criteria

- Package imports succeed.
- Shared package modules import without side effects.
- `uv run harnessbuddy --help` exits 0.
- `uv run harnessbuddy generate --help` exits 0.
- `uv run pytest -q` passes.
- `uv run ruff check` passes.
- `uv run ty check` passes.
