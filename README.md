# HarnessBuddy

HarnessBuddy is a CLI that takes the manual effort out of building C/C++ libraries for
fuzzing while aiming to minimize LLM token cost. It detects
the build system, builds the library, probes harness compilation to find linker
dependencies, and writes an artifacts that builds and links the library against a harness.

## What it does

1. Detects the build system: CMake, Meson, Autotools, or Makefile.
2. Builds the library with a deterministic script. If that build fails, an agent repairs it.
3. Probes harness compilation to find the transitive linker dependencies. If the probe fails,
   an agent repairs it.
4. Writes the output directory `<output>/<project>/`.

## Output

The output directory provides a reproducible OSS-Fuzz project and a standalone host build tree. `docker build .` works, and so does `./setup.sh && ./build_library.sh`.

```
output/<project>/
├── Dockerfile             # OSS-Fuzz project files
├── project.yaml
├── .dockerignore
├── setup.sh               # host counterpart of the Dockerfile: clone + apt dependencies
├── build.sh               # build_library.sh, then compile_harnesses.sh
├── build_library.sh       # build and install the library into install/
├── compile_harness.sh     # compile and link one harness source
├── compile_harnesses.sh   # compile every source in harness_source/ into out/
├── harness_source/        # the harness sources, with a compiling stub to start from
├── install/               # the library this run built
├── compile_commands.json  # when capture succeeded
├── stats.json             # what this run did: durations, agent use, status
└── README.md              # what this run verified, and how to run it
```

`harness_source/` starts with a stub — an `LLVMFuzzerTestOneInput` with a
`// TODO: Add fuzzing logic` body. HarnessBuddy proves the harness *compiles and links*
against the library. You write the fuzzing logic.

## Environments

`--environment` selects where this run builds and verifies. It does not change what is
generated: the output directory always holds both setups. The generated `README.md` records
which of the two this run exercised.

- `local` — build and verify on the host.
- `oss-fuzz` — build and verify in an OSS-Fuzz compatible Docker container, then prove the
  generated Dockerfile builds from scratch.

## Prerequisites

- Python 3.13 and [`uv`](https://docs.astral.sh/uv/)
- Docker, if you use `--environment oss-fuzz`
- [`bear`](https://github.com/rizsotto/Bear) captures `compile_commands.json` for Make and Autotools builds for future program analysis.
- For local builds: `cmake`, `make`, `autoconf`/`automake`, and `meson` on `PATH`. The below list is also a noncomprehensive set of common libraries that many projects depend on.
  ```bash
  sudo apt update
  sudo apt install -y \
      build-essential \
      cmake \
      ninja-build \
      meson \
      autoconf \
      automake \
      libtool \
      gettext \
      autopoint \
      pkg-config \
      m4 \
      flex \
      bison \
      python3 \
      python3-pip \
      perl \
      git \
      curl \
      wget \
      unzip \
      zip \
      xz-utils \
      file \
      bear \
      cloc
  ```

## Install

```bash
uv sync
```

## Quickstart

```bash
uv run harnessbuddy generate https://github.com/madler/zlib.git
```

This clones the repository, builds it, verifies it, and writes `output/zlib/`. Useful flags:

```bash
# Verify in the OSS-Fuzz Docker environment instead of on the host
uv run harnessbuddy generate <REPO_URL> --environment oss-fuzz

# Turn off agent repair: a failed build then simply fails the run
uv run harnessbuddy generate <REPO_URL> --no-agents

# Pass build-system configure options (repeat for more than one)
uv run harnessbuddy generate <REPO_URL> --library-configure-arg=-DCARES_STATIC=ON

# Skip the from-scratch rebuild that validates an agent's repair, for a faster run
uv run harnessbuddy generate <REPO_URL> --bypass-scratch-validation

# Use distinct library and final-harness instrumentation defaults
uv run harnessbuddy generate <REPO_URL> --environment local \
  --cc clang --cxx clang++ \
  --library-cflags='-fsanitize=fuzzer-no-link,address' \
  --library-cxxflags='-fsanitize=fuzzer-no-link,address' \
  --harness-cflags='-fsanitize=fuzzer,address' \
  --harness-cxxflags='-fsanitize=fuzzer,address'
```

Agent repair is on by default and calls a paid network service. `--agent claude` (the
default) or `--agent codex` selects the backend; `--no-agents` turns it off.

A run normally proves its result by rebuilding the library into an empty tree once. It only
needs to do that when a repair agent changed the build, since the deterministic build already
starts from nothing. `--bypass-scratch-validation` drops that rebuild on the agent lane too,
and on `--environment oss-fuzz` also skips the unmounted Dockerfile build. The result is
faster but unproven, so `stats.json` and the generated `README.md` both record that it ran.

See `uv run harnessbuddy generate --help` for the full set of options (custom output
location, pinning a branch/tag/commit, a different base image, etc).

### Adaptable builds

The generated build scripts let you supply your own toolchain and flags:

```bash
CC=afl-clang-fast CXX=afl-clang-fast++ \
CFLAGS=-fsanitize=address CXXFLAGS=-fsanitize=address \
  ./build_library.sh
```

### When a run fails

HarnessBuddy keeps its working state in `.harnessbuddy/<project>/`: the workspace it built
in, `logs/` with the raw output of each phase, and `stats.json`. A failed run writes no
output directory, but the library build artifacts stay in `.harnessbuddy/<project>/install/`
if you want to debug the link line there. Add `--log-level debug` for more detail, or
`--quiet` to hide the raw subprocess output while a phase runs.

## Other commands

`generate` produces a `compile_commands.json` file that two follow-on commands consume, to
extract the library's API surface (`extract-features`) and to produce an OSS-Fuzz-Gen
compatible YAML input (`generate-yaml`).

```bash
uv run harnessbuddy extract-features <BUILD_PATH>   # -> features.json (run this first)
uv run harnessbuddy generate-yaml <BUILD_PATH>      # -> <project>.yaml
```

`extract-features` runs a Clang LibTooling-based parser over every header and source file in
`compile_commands.json`. It writes `<BUILD_PATH>/features.json`, a structured inventory of
the library's C/C++ declarations:

- `functions` — name, return type, parameters, full signature, declaring header, and
  whether it is public API (declared in a header, not `static`)
- `typedefs` — name, underlying type, declaring header
- `macros` — name, object- or function-like, parameters (if function-like), value,
  declaring header
- `enums` — name (if any), enumerators with their values, declaring header
- `records` — structs and unions: name (if any), `kind`, fields, declaring header
- `warnings` — non-fatal problems found during the parse

`generate-yaml` reads `features.json` and keeps only the public functions. To get
finer-grained output, name one or more headers and it keeps only the functions those headers
declare:

```bash
uv run harnessbuddy generate-yaml <BUILD_PATH> zlib.h zconf.h
```

Run `uv run harnessbuddy --help` for details.

### Other Command Prerequisites
- `libclang-20` is required for feature extraction functionality.

