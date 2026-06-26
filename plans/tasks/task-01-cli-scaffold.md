# Task 1: Python CLI Scaffold

**Status**: completed

## Summary

Create the initial Python package and command-line surface with the full
`generate` argument surface and parsing tests.

## Requirements

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
- The command may return a clear "not implemented" message after argument
  validation.
- Add a minimal test suite for CLI parsing.

## Acceptance Criteria

- `uv run harnessbuddy --help` works.
- `uv run harnessbuddy generate --help` works.
- `uv run pytest -q` passes.
- `uv run ruff check` passes.
- `uv run ty check` passes.
