# Task 3: oss-fuzz Project File Generation

**Status**: completed

## Summary

Generate a complete oss-fuzz project skeleton from a static `AnalysisResult`.

## Requirements

- Create an output directory named after the project under the parent
  `--output DIR`. Fail with `OutputDirectoryExistsError` if it already exists.
- Generate all seven files:
  - `project.yaml` — `homepage` (clone URL) and `language` only; no contact
    metadata.
  - `Dockerfile` — clones the repository URL into `$SRC/<project-name>`;
    adds a `git checkout` line only when `--repo-ref` is provided; copies
    harness sources and build scripts.
  - `build.sh` — orchestration only: calls `build_library.sh` then
    `compile_harnesses.sh`.
  - `build_library.sh` — shebang, `set -euo pipefail`, detected build-system
    comment, stub `build.env` for Task 6 to fill in.
  - `compile_harnesses.sh` — sources `build.env`, loops over `.c`/`.cc`/
    `.cpp`/`.cxx` harness files, uses `$CC`/`$CXX` per extension, links with
    `$LIB_FUZZING_ENGINE`.
  - `harness_source/default_fuzzer.cc` — minimal `LLVMFuzzerTestOneInput`,
    no `main`.
  - `provenance.json` — build strategy, detected signals (build files and
    headers as relative paths), clone URL, optional repo ref, output path,
    warnings.
- All generated shell scripts must start with `#!/bin/bash` and
  `set -euo pipefail`.

## Acceptance Criteria

- Snapshot tests verify generated files for every fixture build-system type.
- Dockerfile tests cover generation with and without `--repo-ref`.
- Generated shell scripts are deterministic.
- Generation fails safely when the target output directory already exists.
