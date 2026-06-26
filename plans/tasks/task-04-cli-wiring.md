# Task 4: CLI Wiring

**Status**: pending  
**GitHub Issue**: https://github.com/gabe-sherman/harnessbuddy/issues/18

## Summary

Wire the existing ingestion, analysis, and generation pipeline into a working
`harnessbuddy generate` command. Currently `_cmd_generate` returns
"not implemented yet".

## Requirements

- `_cmd_generate` in `cli.py` must run the full pipeline:
  1. Detect whether `REPO_URL` is a local path or a URL; dispatch to
     `ingest_local` or `ingest_url` accordingly.
  2. Apply `--project-name` override if provided.
  3. Run `analyze()` on the ingested source.
  4. Run `generate()` with `--output DIR` as the parent directory. Default
     `--output` to the current working directory when not specified.
  5. Apply `--repo-ref` to ingestion and generation.
- Handle errors with actionable, human-readable messages and non-zero exit codes:
  - `RepositoryNotFoundError` → `"Repository not found: ..."`
  - `NoCloneableOriginError` → `"No cloneable git origin found. Provide a URL
    instead of a local path, or add a remote origin."`
  - `UnsupportedRepositoryError` → `"No C/C++ build signals found in this
    repository."`
  - `OutputDirectoryExistsError` → pass through the existing message
- Print a concise success summary:
  - output path
  - project name
  - detected build system
  - language
  - any warnings

## Acceptance Criteria

- `uv run harnessbuddy generate <local-fixture-path>` runs the full pipeline
  end-to-end.
- Integration-style CLI tests using local fixture repos (no network) cover the
  success path and each error path.
- Error paths return a non-zero exit code.
- `uv run pytest -q`, `uv run ruff check`, and `uv run ty check` all pass.
