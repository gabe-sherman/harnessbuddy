# Library Builder OSS-Fuzz Workflow

## Summary

`harnessbuddy generate <REPO_URL>` produces two output trees for a C/C++ library:
a **local** skeleton for host-native development and an **oss-fuzz** project
directory ready to submit.

---

## Pipeline Steps

### 1. Ingest
`ingest_url` or `ingest_local` → `RepositorySource`

- URL → clone into `~/.local/share/harnessbuddy/<project>/src`
- Local path → read origin URL for Dockerfile, use path as-is
- `NoCloneableOriginError` raised when a local path has no remote origin
  (the clone URL is required for Dockerfile generation)

### 2. Analyze
`analyze(source)` → `AnalysisResult`

Walks the repository tree looking for:
- Build system files (CMakeLists.txt, meson.build, configure, Makefile, etc.)
- C/C++ headers (`.h`, `.hpp`, etc.)
- Language (C, C++, mixed)
- Autotools variant (`AutotoolsSetup`: configure-present, autogen.sh, autoreconf)

Raises `UnsupportedRepositoryError` if no C/C++ build signals found.

### 3. Load persisted state
Two state sources are loaded and merged before the build runs.

**3a. `load_system_deps(analysis)`** reads `<source_dir>/system_deps.json` if present
and populates `analysis.system_packages`. The agent writes this file when it
identifies required apt packages so subsequent runs embed them without re-invoking
the agent.

**3b. `load_project_state(state_file)`** reads `.harnessbuddy/<project>/state.json`
(a per-project accumulator across all runs). If agent packages were just loaded via
3a, they are immediately merged into state and saved.

State schema:
```json
{
  "version": 1,
  "apt_packages": ["libssl-dev", "libzstd-dev"],
  "brew_packages": ["openssl", "zstd"],
  "unknown_libs": [],
  "sources": {
    "agent": ["libssl-dev"],
    "linker": ["libzstd-dev"]
  }
}
```

`sources` is for human debugging only; generators read only `apt_packages` /
`brew_packages`. Packages are never removed from state, only unioned in.

### 4. Host build exploration
`explore(analysis, workspace)` → `BuildExplorationResult`

- Writes `build_library.sh` into the source tree with absolute paths to
  `workspace/build/` and `workspace/install/`
- Runs it via `bash build_library.sh` from `source_path`
- Validates `install/lib/*.a` and `install/include/` both exist and are non-empty
- On UNKNOWN build system: returns immediately with `succeeded=False`

### 5. Agent fallback (optional)
`invoke_library_builder_agent(analysis, result, workspace, tool=agent)`

Triggered when `explore` fails and `--agent claude|codex` is set.

- Reads `agents/library_builder/SKILL.md` for the prompt if the file exists
- Streams output to terminal in real time
- After agent exits, re-runs `_validate_install_artifacts` — the agent must leave
  `install/lib/*.a` and `install/include/` populated
- Raises `LLMBudgetError` on session/quota limit patterns
- Raises `BuildFailureError` on `ACTION_REQUIRED` sentinel (user must resolve manually)
- Sets `result.llm_used = True` on the returned `BuildExplorationResult`

**system_deps.json contract:** The agent SKILL should write this file when it
installs packages, so `load_system_deps` can pick them up on the next run.

### 6. Harness compilation exploration
`explore_harness_compilation(install_dir, workspace, language)` → `HarnessExplorationResult`

Probes harness compilation against installed `*.a` files to discover transitive
system library dependencies before real harnesses exist.

**Symbol probing:** Uses `--whole-archive` (Linux) / `-all_load` (macOS) to link a
minimal stub against all static libs. Linker errors are parsed for undefined symbol
names using three patterns:
- Linux: `undefined reference to 'sym'`
- macOS compact: `Undefined symbol: _sym`
- macOS verbose: `"_sym", referenced from:`

`symbol_patterns.json` maps each `-l` flag to a list of anchored regex patterns
(e.g. `^pthread_`, `^sin[fhl]?$`). Up to 5 retries accumulate discovered `-l` flags.

**Missing-lib detection:** `_extract_missing_system_libs(stderr)` separately parses:
- macOS: `ld: library 'X' not found`
- Linux: `ld: cannot find -lX`

These raw lib names are stored in `HarnessExplorationResult.missing_system_libs`.

Returns `HarnessExplorationResult` with `static_libs`, `include_dir`,
`transitive_link_flags`, and `missing_system_libs` regardless of success/failure.

### 7. Package translation and state update
If `harness_result.missing_system_libs` is non-empty, translate raw lib names to
installable packages via `translate(lib_names) → PackageTranslation`.

`package_names.json` maps lib names (e.g. `"zstd"`) to apt/brew names. System libs
(`m`, `pthread`, `dl`, `rt`, `resolv`, etc.) are silently dropped. Unmapped libs
go into `PackageTranslation.unknown_libs` and surface as CLI warnings.

The translation result is immediately merged into `state.json` under `source_tag="linker"`
and saved. The accumulated state is then applied to analysis:

```python
analysis.system_packages = state["apt_packages"]   # flows into Dockerfile
brew_packages = state["brew_packages"]             # passed directly to generate_local
```

### 8. Harness failure handling
If harness exploration failed:

- **With `missing_system_libs`:** print an actionable apt/brew install hint and
  **fall through to generation** — output files are produced with the discovered
  packages baked in. The user installs the missing libs and re-runs.
- **Without `missing_system_libs`:** hard bail (`return 1`) — no diagnosis available.

### 9. Dual output generation
Both generators receive `(analysis, output_parent, exploration, harness_exploration)`.

#### Local output — `generate_local(..., brew_packages=brew_packages)`
Output: `<output>/<project>/output/local/`

| File | Purpose |
|------|---------|
| `setup.sh` | Clone repo + platform-appropriate dep install |
| `build_library.sh` | Host-native build (with `CC/CXX` fallbacks) |
| `build_harness.sh` | Compile harnesses against discovered static libs |
| `harness_src/default_fuzzer.c` | Stub fuzzer (`.c` or `.cc` based on language) |

`setup.sh` install command is chosen at generation time via `sys.platform`:
- macOS + brew packages present → `brew install ...`
- Otherwise with apt packages → `apt-get install -y --no-install-recommends ...`
- Neither → `# TODO: install build dependencies for this library`

`build_harness.sh` has concrete `*.a` paths and embedded `EXTRA_LINK_FLAGS` from the
harness exploration result. No `build.env` sourcing required.

#### OSS-Fuzz output — `generate_oss_fuzz()`
Output: `<output>/<project>/output/oss-fuzz/`

| File | Purpose |
|------|---------|
| `project.yaml` | `homepage` + `language` |
| `Dockerfile` | `apt install` + `git clone` + `COPY` scripts |
| `build.sh` | Calls `build_library.sh` then `compile_harnesses.sh` |
| `build_library.sh` | Build against `$SRC/<project>` (no CC fallbacks) |
| `compile_harnesses.sh` | Loop over `harness_source/`, compile each `.c`/`.cc` |
| `harness_source/default_fuzzer.cc` | Stub C++ fuzzer |
| `provenance.json` | Full build metadata snapshot |

`Dockerfile` includes a `RUN apt-get install` line for `analysis.system_packages`
(which now includes all linker-discovered packages from state). OSS-Fuzz images are
always Debian-based, so brew packages are never used here.

`compile_harnesses.sh` uses `$CC`/`$CXX`, `$HB_INCLUDE_FLAGS`, `$HB_LIBRARY_FLAGS`,
and `$LIB_FUZZING_ENGINE` — all injected by the OSS-Fuzz base builder image.

---

## Key Invariants

- **`install/lib/*.a` + `install/include/` must both be non-empty** before generation
  runs. Both `explore` and the agent path validate this via `_validate_install_artifacts`.
- **`state.json` is additive** — packages are only unioned in, never removed. Re-runs
  accumulate without duplicating entries.
- `build_library.sh` is written into the source tree during exploration but NOT
  copied to the output — the output has its own freshly generated version.
- `system_deps.json` lives in the source tree; `state.json` lives in the HarnessBuddy
  state dir. Both persist independently across runs.
- Generation proceeds even on partial harness failure when missing libs are identified —
  output files are still produced so the user can see what packages are needed.

---

## What Comes Next

### Harness builder agent (step 10)
After the library builds, a harness agent should:
1. Inspect `install/include/` to enumerate public APIs
2. Generate harness sources that exercise those APIs
3. Verify they compile against the installed library
4. Place final sources in `harness_source/`

### OSS-Fuzz Docker validation
`--skip-validation` is currently a no-op. The real implementation should run
`python infra/helper.py build_fuzzers <project>` against the generated output
and report errors.
