#!/usr/bin/env bash
# The single definition of "the build passed", for every environment.
#
# Runs the project's own build.sh (build_library.sh then compile_harnesses.sh) from nothing and
# asserts the artifacts that make the output worth shipping. The pipeline and every repair agent
# invoke this same script, so a fix an agent verifies is a fix the pipeline accepts.
#
# --keep-artifacts keeps install/ and build/, which skips the library rebuild. See the comment
# on the deletion below for who is allowed to pass it.
#
# Two fallbacks let one script text run unchanged on the host and in the OSS-Fuzz container:
# $OUT falls back to <workspace>/out, and the build is entered through `compile` wherever that
# exists and through build.sh otherwise.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <workspace> [--keep-artifacts]" >&2
  exit 1
fi

workspace=$1
keep_artifacts=0
if [[ $# -eq 2 ]]; then
  if [[ $2 != "--keep-artifacts" ]]; then
    echo "Unknown option: $2 (expected --keep-artifacts)" >&2
    exit 1
  fi
  keep_artifacts=1
fi

cd "$workspace"

OUT="${OUT:-$PWD/out}"
export OUT

# Deleting install/ and build/ is what makes this a from-scratch check. --keep-artifacts drops
# that, leaving build_library.sh's own skip-if-already-built guard to short-circuit the library
# build. Only a caller that already built the library from nothing may pass it -- the pipeline
# does so on the lane where explore() just did exactly that. A repair agent never passes it,
# because its whole job is changing the build, so its fix has to survive a real cold build.
if [[ $keep_artifacts -eq 0 ]]; then
  rm -rf install build
fi
# $OUT is emptied rather than removed: check_build_in_container.sh bind-mounts the host's out/
# there, and a mountpoint cannot be unlinked from inside the container ("Device or resource
# busy"), which under set -e would fail the gate before the build even started.
#
# Emptied even under --keep-artifacts: compile_harnesses.sh always runs, so the $OUT assertion
# below stays a real check on this invocation rather than on a leftover binary.
mkdir -p "$OUT"
find "$OUT" -mindepth 1 -delete

# `compile` runs build.sh, but only after assembling what the base image half-provides:
# SANITIZER_FLAGS resolved into CFLAGS/CXXFLAGS, and LIB_FUZZING_ENGINE=-fsanitize=fuzzer in
# place of the deprecated archive path its ENV names. Running build.sh directly in that image
# links against an archive that does not exist yet, and working that around yields an
# uninstrumented target. A host has no `compile` and nothing to assemble.
build_command=(bash build.sh)
if command -v compile > /dev/null 2>&1; then
  build_command=(compile)
fi

echo "=== build.sh ==="
if command -v bear > /dev/null 2>&1; then
  # Captures compile_commands.json for Make/Autotools, which have no build-system equivalent.
  # Harmless for the others, which emit their own.
  bear -- "${build_command[@]}"
else
  "${build_command[@]}"
fi

if ! compgen -G "install/lib/*.a" > /dev/null; then
  echo "FAILED: no static libraries (*.a) found in install/lib" >&2
  exit 1
fi

if [[ ! -d install/include ]] || [[ -z "$(ls -A install/include)" ]]; then
  echo "FAILED: install/include is missing or empty" >&2
  exit 1
fi

if [[ ! -d "$OUT" ]] || [[ -z "$(ls -A "$OUT")" ]]; then
  echo "FAILED: $OUT is missing or empty — no harness binary was produced" >&2
  exit 1
fi

echo "OK: build.sh succeeded and install/ plus $OUT are populated"
