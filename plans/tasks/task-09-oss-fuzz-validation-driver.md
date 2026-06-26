# Task 9: oss-fuzz Validation Driver

**Status**: pending

## Summary

Implement validation through a local oss-fuzz checkout, running the standard
`helper.py` build pipeline against the generated project.

## Requirements

- Auto-clone or update `google/oss-fuzz` into HarnessBuddy state when
  validation is enabled.
- Sync the generated project into `oss-fuzz/projects/<project-name>`.
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
- Missing Docker and missing network must return actionable validation failures
  and must not trigger agent fallback.

## Acceptance Criteria

- Mocked helper tests cover success.
- Mocked helper tests cover build failure.
- Mocked helper tests cover missing Docker.
- Mocked helper tests cover missing network.
