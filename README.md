# harnessbuddy

A developer tool for preparing C/C++ libraries for fuzzing with [OSS-Fuzz](https://github.com/google/oss-fuzz). Given a repository URL or local path, harnessbuddy analyzes the build system, detects headers, and generates an OSS-Fuzz-compatible project directory with build scripts and harness compilation scaffolding.

## How it works

1. **Ingest** — clone a remote repo or accept a local path
2. **Analyze** — detect the build system (CMake, Meson, Autotools, Makefile, Ninja) and C/C++ headers
3. **Generate** — produce an OSS-Fuzz project directory with `project.yaml`, `Dockerfile`, `build.sh`, and harness stubs
4. **Validate** — optionally verify the generated project builds inside the OSS-Fuzz Docker environment
5. **Agent fallback** — when deterministic analysis is insufficient, delegate to a Codex or Claude agent

## Installation

Requires Python 3.13 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Usage

```bash
uv run harnessbuddy generate REPO_URL [options]
```

**Arguments:**

| Flag | Description |
|------|-------------|
| `REPO_URL` | Repository URL or local path (required) |
| `--output DIR` | Parent directory for generated output |
| `--project-name NAME` | Override the inferred project name |
| `--repo-ref REF` | Git branch, tag, or commit to check out |
| `--skip-validation` | Skip OSS-Fuzz Docker validation |
| `--no-agents` | Disable agent fallback entirely |
| `--agent {auto,codex,claude}` | Agent backend to use (default: `auto`) |
| `--allow-host-build` | Allow host-side build exploration |
| `--keep-workdir` | Keep the working directory after the run |

**Examples:**

```bash
# Generate harness scaffolding for a remote library
uv run harnessbuddy generate https://github.com/example/mylib

# Use a local checkout, write output to ./out
uv run harnessbuddy generate /path/to/mylib --output ./out

# Skip validation and pin to a specific tag
uv run harnessbuddy generate https://github.com/example/mylib --repo-ref v2.1.0 --skip-validation
```

Working state is stored under `.harnessbuddy/` in the current directory.

## Running tests

```bash
uv run pytest -q          # fast run
uv run pytest -v          # verbose output
uv run pytest tests/core/ # single module
```

No network access or Docker is required for the test suite.

## Linting and type checking

```bash
uv run ruff check         # lint
uv run ruff check --fix   # auto-fix
uv run ruff format        # format
uv run ty check           # type checking
```

## Project structure

```
src/harnessbuddy/
├── cli.py                     # Argument parsing and command dispatch
├── core/
│   ├── paths.py               # State directory helpers
│   └── repos.py               # Repository ingestion (clone or local)
└── library_builder/
    ├── models.py              # BuildSystem, Language, AnalysisResult
    ├── analysis.py            # Deterministic build system & header detection
    ├── generation.py          # OSS-Fuzz project generation (in progress)
    ├── validation.py          # Build validation (in progress)
    └── agents.py              # LLM agent fallback orchestration (in progress)
```

## Status

Repository ingestion and static analysis are complete. Project generation, validation, and agent fallback are under active development. See [`STATUS.md`](STATUS.md) for current progress.
