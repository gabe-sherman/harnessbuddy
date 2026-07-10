#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]] || [[ $# -gt 3 ]]; then
  echo "Usage: $0 <oss_fuzz_project_dir> <project_name> [harness_name]" >&2
  exit 1
fi

oss_fuzz_project_dir=$1
project_name=$2
harness_name=${3:-}

tag="${project_name}:harnessbuddy-check"

cd "$oss_fuzz_project_dir"

if ! docker build -t "$tag" .; then
  echo "FAILED: docker build failed for $oss_fuzz_project_dir" >&2
  exit 1
fi

if [[ -n "$harness_name" ]]; then
  check="test -x \"/out/${harness_name}\""
else
  check='test -n "$(ls -A /out)"'
fi

if ! docker run --rm --entrypoint bash "$tag" -c "compile && ${check}"; then
  echo "FAILED: compile (build.sh) or the artifact check failed inside the container" >&2
  exit 1
fi

echo "OK: docker build and in-container compile succeeded"
