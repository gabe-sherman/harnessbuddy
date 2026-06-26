# Task 11: End-To-End CLI Polish

**Status**: pending

## Summary

Wire all stages into a complete, polished v1 workflow with actionable error
messages and a concise final report.

## Requirements

- `harnessbuddy generate` must run in order:
  1. input handling
  2. static analysis
  3. host-side build exploration (if `--allow-host-build`)
  4. project generation
  5. validation (unless `--skip-validation`)
  6. agent fallback (unless `--no-agents`)
  7. final reporting
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
- Reports must call out failed agent edits left in place and validation
  failures caused by missing Docker or network.
- Make error messages clear and actionable for:
  - unsupported repository
  - clone failure
  - no C/C++ signals
  - validation failure
  - missing Docker
  - missing agent executable

## Acceptance Criteria

- Full non-Docker test suite passes.
- Lint and type checks pass with zero warnings.
- Generated scripts are reviewed for unnecessary complexity.
