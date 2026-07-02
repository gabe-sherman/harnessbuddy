# CLI Contract: `extract-features` and `generate-benchmark`

Both are new `harnessbuddy` subcommands (FR-016), registered in `cli.py`
alongside the existing `generate` subcommand, following the same
argparse-dispatch pattern.

## `harnessbuddy extract-features <output-dir>`

Extracts a library's declarations into a JSON `FeatureArtifactSet`
(FR-001–FR-010).

- **Argument**: `output-dir` — any directory containing a
  `compile_commands.json` at its root. Typically the per-project output
  directory `library_builder` already produced (the shared parent of
  `local/` and `oss-fuzz/`), but any directory with a valid
  `compile_commands.json` is accepted — e.g. a raw CMake build directory
  such as `zlib_feature_test/`, used directly by this feature's own
  integration tests and quickstart (no `library_builder` run required to
  exercise `extract-features` against it).
- **Behavior**: Builds the native tool once if not already cached (research
  §2), invokes it against `<output-dir>/compile_commands.json`, validates
  the result against `contracts/feature-artifact.schema.json`, and writes it
  to `<output-dir>/features.json` (overwriting any prior run, per FR-015).
- **Exit codes**:
  - `0`: extraction succeeded (an empty `functions`/`typedefs`/etc. list is
    still success — spec edge case: header-only library).
  - `1`: `<output-dir>/compile_commands.json` missing or unreadable (FR-003;
    also covers the spec edge case of an `output-dir` with no recognizable
    build artifacts at all) — message names the expected path and how to
    produce one (CMake `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` or
    `bear -- make`).
  - `1`: native tool build failure (e.g. missing LLVM/Clang development
    libraries, or `find_package(Clang)`/`find_package(LLVM)` failing to
    locate a usable LibTooling installation) — message surfaces the build
    tool's own error, not an opaque failure.

## `harnessbuddy generate-benchmark <output-dir>`

Converts an existing `features.json` into an oss-fuzz-gen-compatible YAML
benchmark file (FR-011–FR-014).

- **Argument**: `output-dir` — same directory as above; must already contain
  `features.json` from a prior `extract-features` run.
- **Optional overrides**: `--target-name` (default `default_fuzzer`),
  `--target-path` (default `/src/harness_source/default_fuzzer.{ext}`, `ext`
  from the artifact's `language`).
- **Behavior**: Loads `<output-dir>/features.json`, filters `functions` to
  `is_public_api == true` (FR-012), builds a `BenchmarkYaml`, validates it
  against `contracts/benchmark-yaml.schema.json`, and writes
  `<output-dir>/<project_name>.yaml` (overwriting any prior run, per
  FR-015).
- **Exit codes**:
  - `0`: conversion succeeded.
  - `1`: `features.json` missing — message tells the user to run
    `extract-features` first.
  - `1`: `features.json` fails schema validation (corrupted or produced by
    an incompatible `schema_version`) — message names the mismatch.
