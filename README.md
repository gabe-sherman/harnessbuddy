# HarnessBuddy

HarnessBuddy is a CLI that automates preparing a C/C++ library for fuzzing. Point it at a
repository URL and it detects the build system, builds the library, probes harness
compilation to discover linker dependencies, and generates either a standalone dev
scaffold or a ready-to-drop-in OSS-Fuzz project.

> **Status:** under active development.

## What it does

- Detects the build system (CMake, Meson, Autotools, or Makefile).
- Builds the library locally to validate it and collect install artifacts.
- Probes harness compilation to discover transitive linker dependencies.
- Generates one output directory, chosen by `--environment` (default `local`):
  - `local/` — a standalone dev scaffold (`setup.sh`, `build_library.sh`,
    `build_harness.sh`, `harness_src/`)
  - `oss-fuzz/` — a project laid out to drop into an existing OSS-Fuzz checkout's
    `projects/<name>/` and build with its own tooling (`project.yaml`, `Dockerfile`,
    `build.sh`, `build_library.sh`, `compile_harnesses.sh`, `harness_source/`), with
    `--environment oss-fuzz`
- Optionally falls back to an LLM agent (`--agent claude` or `--agent codex`) to repair
  build scripts when a static build fails.

The generated `harness_src/`/`harness_source/` always contains a compiling stub
(`LLVMFuzzerTestOneInput` with a `// TODO: Add fuzzing logic` body) — HarnessBuddy proves
the harness *compiles and links* against the library, but you still write the actual
fuzzing logic.

## Prerequisites

- Python 3.13 and [`uv`](https://docs.astral.sh/uv/)
- `cmake`, `make`, `autoconf`/`automake`, and `meson` on `PATH` (build systems to support local builds)
- Docker, if you want to validate against the `oss-fuzz` environment
  (`--environment oss-fuzz`)
- [`bear`](https://github.com/rizsotto/Bear) (`apt install bear` / `brew install bear`),
  used to capture `compile_commands.json` for Make/Autotools builds on the local host.
  Best-effort — if it's missing, capture is skipped and reported.

## Install

```bash
uv sync
```

## Quickstart

```bash
uv run harnessbuddy generate https://github.com/madler/zlib.git
```

This clones the repository, builds it, and writes a `local/` project directory for it.
Useful flags:

```bash
# Build and validate in the OSS-Fuzz Docker environment instead, writing oss-fuzz/
uv run harnessbuddy generate <REPO_URL> --environment oss-fuzz

# If first build pass fails, fall back to an agent to resolve issues
uv run harnessbuddy generate <REPO_URL> --agent claude
```

See `uv run harnessbuddy generate --help` for the full set of options (custom output
location, pinning a branch/tag/commit, skipping validation, etc).

### Customizing the local build

`local/build_library.sh` and `build_harness.sh` respect the standard compiler/sanitizer
env vars, so you can rebuild the same project with a different toolchain — for example,
AFL++ with an ASan-instrumented build:

```bash
CC=afl-clang-fast CXX=afl-clang-fast++ \
CFLAGS=-fsanitize=address CXXFLAGS=-fsanitize=address \
  ./local/build_library.sh
```

`BUILD_PREFIX` controls where the library is installed (defaults to the project
directory).

## Other commands

`generate` produces a `compile_commands.json` file that two follow-on commands can consume to
extract various library artifacts (`extract-features`) and produce OSS-Fuzz-Gen compatible YAML inputs (`generate-yaml`).

```bash
uv run harnessbuddy extract-features <BUILD_PATH>   # -> features.json (Must run first)
uv run harnessbuddy generate-yaml <BUILD_PATH>       # -> benchmark YAML
```

`extract-features` runs a Clang LibTooling-based parser over every header/source file in
`compile_commands.json` and writes `<BUILD_PATH>/features.json`, a structured inventory of
the library's C/C++ declarations:

- `functions` — name, return type, parameters, full signature, declaring header, and
  whether it's public API (declared in a header, not `static`)
- `typedefs` — name, underlying type, declaring header
- `macros` — name, object- vs. function-like, parameters (if function-like), value,
  declaring header
- `enums` — name (if any), enumerators with their values, declaring header
- `records` — structs/unions: name (if any), `kind`, fields, declaring header
- `warnings` — non-fatal issues hit while parsing

`generate-yaml` reads `features.json` and keeps only `is_public_api: true` functions to
build an OSS-Fuzz-Gen compatible benchmark YAML.

Run `uv run harnessbuddy --help` for details.
