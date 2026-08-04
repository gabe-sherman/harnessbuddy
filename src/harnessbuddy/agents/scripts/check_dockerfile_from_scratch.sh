#!/usr/bin/env bash
# Prove the shipped Dockerfile builds and compiles with nothing mounted.
#
# The gate (check_build_in_container.sh) mounts the workspace, which is what makes the artifacts
# reachable — but a mounted run can pass while the Dockerfile's own clone or apt layers are
# broken, since the mount supplies what the image failed to. This runs the real thing:
# `docker build`, then `compile`, no mounts, then a check for a harness binary in /out.
#
# Runs once, as the last step before generation, for an oss-fuzz target.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <project_dir> <project_name>" >&2
  exit 1
fi

project_dir=$1
project_name=$2
tag="${project_name}:harnessbuddy-from-scratch"

cd "$project_dir"

if ! docker build -t "$tag" .; then
  echo "FAILED: docker build failed for $project_dir" >&2
  exit 1
fi

if ! docker run --rm --entrypoint bash "$tag" -c 'compile && test -n "$(ls -A /out)"'; then
  echo "FAILED: compile (build.sh) or the /out check failed inside the container" >&2
  exit 1
fi

echo "OK: from-scratch docker build and in-container compile succeeded"
