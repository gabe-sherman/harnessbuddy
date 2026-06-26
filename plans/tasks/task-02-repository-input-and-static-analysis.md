# Task 2: Repository Input and Static Analysis

**Status**: completed

## Summary

Implement repository ingestion and deterministic static analysis of C/C++
build-system signals.

## Requirements

- Support repository URLs (`ingest_url`): clone into `.harnessbuddy/` run state.
- Support local paths (`ingest_local`): validate path, read git origin URL for
  Dockerfile generation, fail clearly if no cloneable origin is available.
- Infer project name from `--project-name` or the repository basename.
- Detect C/C++ build-system signals in priority order:
  - `CMakeLists.txt`
  - `meson.build`
  - `configure.ac`, `configure.in`, or `configure`
  - `Makefile` or `makefile`
  - `build.ninja`
- Detect public headers and likely language from file extensions (`.h` → C,
  `.hpp`/`.hxx`/`.hh` → C++, mix → C and C++).
- Do not execute target repository scripts.
- Return a structured `AnalysisResult` containing:
  - project name
  - source location
  - selected build system
  - detected build files
  - detected headers
  - language
  - clone URL
  - optional repo ref
  - warnings

## Acceptance Criteria

- Fixture tests cover each build-system type.
- Priority ordering is tested (cmake > meson > autotools > makefile > ninja).
- Unsupported repositories (no build signals and no headers) raise
  `UnsupportedRepositoryError` with a clear diagnostic.
- No test requires network access.
