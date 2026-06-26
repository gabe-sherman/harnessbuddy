# HarnessBuddy Architecture

## Overview

HarnessBuddy is a Python CLI that takes a C/C++ repository and produces an
oss-fuzz project directory ready for fuzzing. The pipeline runs in stages:

```
repo URL / local path
       │
       ▼
  [ingestion]          core/repos.py
       │
       ▼
  [static analysis]    library_builder/analysis.py
       │
       ▼
  [host exploration]   library_builder/exploration.py   (--allow-host-build only)
       │
       ▼
  [generation]         library_builder/generation.py
       │
       ▼
  [validation]         library_builder/validation.py    (--skip-validation to omit)
       │
       ▼
  [agent fallback]     library_builder/agents.py        (--no-agents to omit)
```

## Package Structure

```
src/harnessbuddy/
  cli.py                    argparse entrypoint; dispatches to _cmd_generate
  __main__.py               python -m harnessbuddy entry point
  core/
    paths.py                .harnessbuddy/ state dir helpers
    repos.py                ingest_url(), ingest_local(), RepoSource
    subprocesses.py         safe subprocess execution wrapper (timeout, capture)
  library_builder/
    models.py               BuildSystem, Language, AnalysisResult,
                            GenerationResult, BuildExplorationResult,
                            ValidationResult
    analysis.py             analyze() — deterministic build-system detection
    exploration.py          explore() — host-side configure step
    generation.py           generate() — writes oss-fuzz project skeleton
    validation.py           validate() — oss-fuzz helper.py driver
    agents.py               agent fallback subprocess orchestration
    templates/              Dockerfile and shell script template helpers
```

## Key Data Types

| Type | Defined in | Purpose |
|------|-----------|---------|
| `RepoSource` | `core/repos.py` | Cloned/local repo with clone URL and project name |
| `AnalysisResult` | `library_builder/models.py` | Build system, headers, language, warnings |
| `BuildExplorationResult` | `library_builder/models.py` | Host configure outcome: command, stdout, stderr, exit code |
| `GenerationResult` | `library_builder/models.py` | Output path and list of generated files |
| `ValidationResult` | `library_builder/models.py` | oss-fuzz helper exit code, log paths, summary |

## Generated oss-fuzz Project Layout

```
<output-dir>/<project-name>/
  project.yaml              homepage + language
  Dockerfile                clones repo, optionally checks out ref, copies scripts
  build.sh                  calls build_library.sh then compile_harnesses.sh
  build_library.sh          build-system-specific configure + build commands
  compile_harnesses.sh      loops over harness_source/, compiles each fuzzer
  harness_source/
    default_fuzzer.cc       minimal LLVMFuzzerTestOneInput stub
  provenance.json           full record of analysis decisions and exploration results
```

## State Directory

HarnessBuddy keeps all mutable state under `.harnessbuddy/` in the current
working directory (configurable):

```
.harnessbuddy/
  repos/                    cloned repositories
  runs/                     per-run logs, provenance, agent records
  oss-fuzz/                 google/oss-fuzz checkout (for validation)
```

## Design Constraints

- No network access in tests. Fixture repos are tiny local directories.
- No untrusted script execution without `--allow-host-build`.
- Missing Docker or network → validation failure, not agent fallback.
- Agent fallback is one short-lived subprocess per repair attempt; no
  long-running scheduler or daemon.
- All generated shell scripts start with `#!/bin/bash` and `set -euo pipefail`.
