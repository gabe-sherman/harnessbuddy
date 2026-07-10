# Contract: `agents/scripts/` build verification scripts

These scripts are the environment-appropriate verification step an LLM repair agent invokes
after editing `build_library.sh` / `compile_harnesses.sh`, per FR-009 and FR-010. Both are
currently broken (see `research.md` #5) and are rewritten to this contract as part of this
feature.

## `check_local_build.sh`

```
Usage: check_local_build.sh <work_dir>
```

- `work_dir`: the `.harnessbuddy/<project>/` directory containing `build_library.sh` and
  `compile_harnesses.sh` (the same directory the agent has been editing files in).
- Behavior: `cd`s into `work_dir`, runs `bash build_library.sh && bash
  compile_harnesses.sh`.
- Success (exit 0): `install/lib/` contains at least one `*.a`, `install/include/` is
  non-empty, and `out/` is non-empty.
- Failure (nonzero exit): prints which check failed (build command, missing artifact) to
  stderr; does not modify any files.

## `check_docker_build.sh`

```
Usage: check_docker_build.sh <oss_fuzz_project_dir> <project_name> [harness_name]
```

- `oss_fuzz_project_dir`: the generated `oss-fuzz/` project directory (contains
  `Dockerfile`, `build.sh`, `build_library.sh`, `compile_harnesses.sh`).
- `project_name`: used to tag the built image (`<project_name>:harnessbuddy-check`).
- `harness_name` (optional): when given, checks that `/out/<harness_name>` exists and is
  executable after `compile`; when omitted, only checks that `/out/` is non-empty.
- Behavior: `cd`s into `oss_fuzz_project_dir`, runs `docker build -t
  <project_name>:harnessbuddy-check .`, then `docker run --rm --entrypoint bash
  <tag> -c "compile && <artifact-check>"` — the same invocation shape proven in
  `tests/run_ground_truth.py::_docker_build_and_compile`.
- Success (exit 0): both the image build and the in-container compile+artifact-check
  succeed.
- Failure (nonzero exit): prints which step failed (`docker build` vs. `compile`) and the
  relevant command's output to stderr.

## Consumers

- `agents/library_builder/SKILL.md` step 7 and `agents/harness_builder/SKILL.md` step 6 are
  updated to say "run the verification command given in the failure context below" instead
  of manually re-invoking `build_library.sh`/`build_harness.sh` — the concrete command
  (one of the two above, with real arguments) is appended to the prompt by
  `agents.py`'s `build_library_prompt`/`build_harness_prompt` (research.md #6).
- A human debugging a build outside the agent loop can run either script directly with the
  same arguments the agent would have used.
