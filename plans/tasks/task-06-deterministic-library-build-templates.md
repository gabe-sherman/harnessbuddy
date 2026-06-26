# Task 6: Deterministic Library Build Templates

**Status**: pending

## Summary

Implement `build_library.sh` generation with real build commands for each
supported C/C++ build system. This replaces the stub from Task 3 with
per-build-system templates that respect the oss-fuzz compiler environment.

## Requirements

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
- Emit handoff files for harness compilation:
  - include flags
  - library flags
  - static library paths
  - artifact notes
- The handoff contract is `$WORK/harnessbuddy/build.env`, containing shell
  assignments for `HB_INCLUDE_FLAGS` and `HB_LIBRARY_FLAGS`.
- The generated `build_library.sh` is for the oss-fuzz Docker environment; it
  does not run on the host. Host-side build exploration is handled by Task 5.

## Acceptance Criteria

- Generated scripts contain expected commands for each build-system fixture.
- Unit tests prove normal generation does not execute untrusted repository code.
