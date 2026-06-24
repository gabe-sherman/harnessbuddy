# HarnessBuddy Status

## Current State

HarnessBuddy is at the planning and documentation stage.

Current repository files:

- `AGENTS.md`: project summary and development guidance.
- `plans/README.md`: index of scoped implementation plans.
- `plans/library_builder_oss_fuzz.md`: implementation plan for the initial
  library-builder oss-fuzz workflow.
- `STATUS.md`: current project status.

No Python package, CLI, tests, generated oss-fuzz templates, validation driver,
or agent orchestration code has been implemented yet.

## Current Goal

Build a Python CLI that accepts a C/C++ repository URL and generates an
oss-fuzz project with:

- `project.yaml`
- `Dockerfile`
- `build.sh`
- `build_library.sh`
- `compile_harnesses.sh`
- `harness_source/`

The generated `build.sh` should call `build_library.sh` and then
`compile_harnesses.sh`.

## Locked Decisions

- v1 is a Python CLI.
- v1 targets C and C++ libraries.
- Use deterministic analysis before agent fallback.
- Build-system detection order:
  1. CMake
  2. Meson
  3. Autotools/configure
  4. Makefile
  5. Existing Ninja
- Harness sources live in `/src/harness_source` inside the oss-fuzz container.
- `compile_harnesses.sh` compiles every direct C/C++ source file in
  `/src/harness_source`.
- Fuzzer outputs use the harness basename without extension.
- Host-side build execution requires explicit `--allow-host-build`.
- Normal validation should happen through oss-fuzz/Docker.
- zlib is the first planned real integration target.
- zlib integration should use `https://github.com/madler/zlib.git` pinned to
  tag `v1.3.2`.
- Docker/network integration tests should be gated by
  `HARNESSBUDDY_RUN_DOCKER=1`.
- Agent fallback should use Auditron-style subprocess orchestration:
  - Codex through `codex exec`
  - Claude through `claude --print`

## Next Step

Start `plans/library_builder_oss_fuzz.md` Task 0: Initial Project Layout.

Task 0 should create:

- `pyproject.toml`
- `src/harnessbuddy/`
- a `harnessbuddy` console script
- initial CLI help for `harnessbuddy` and `harnessbuddy generate`
- minimal CLI parsing tests

The first code milestone is complete when these commands pass:

```bash
uv run harnessbuddy --help
uv run harnessbuddy generate --help
uv run pytest -q
uv run ruff check
uv run ty check
```

## Known Constraints

- The repo is not currently initialized as a Git repository.
- Normal tests should not require Docker or network access.
- Do not execute untrusted target repository build scripts unless
  `--allow-host-build` is explicitly provided.
- Do not implement real API-aware harness generation in v1; the first milestone
  is build and harness compilation scaffolding.
