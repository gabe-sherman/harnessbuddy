# Task 5: Host-Side Build Exploration

**Status**: pending  
**GitHub Issue**: https://github.com/gabe-sherman/harnessbuddy/issues/19  
**Blocked by**: Task 4 (CLI Wiring)

## Summary

Implement real host-side build exploration behind `--allow-host-build`. When
this flag is passed, HarnessBuddy attempts a configure step for the detected
build system on the host before generating the oss-fuzz skeleton. The goal is
signal collection — does it configure? what flags are needed? — not producing
a full build artifact.

## Requirements

### New file: `src/harnessbuddy/library_builder/exploration.py`

- `explore(analysis: AnalysisResult, workdir: Path, *, timeout: int = 120) -> BuildExplorationResult`
- Per build system, attempt the minimal configure step:
  - **CMake**: `cmake -B <workdir>/build -DCMAKE_INSTALL_PREFIX=<workdir>/install <source_path>`
  - **Meson**: `meson setup <workdir>/build <source_path>`
  - **Autotools**: `./configure --prefix=<workdir>/install` (run in source dir)
  - **Makefile**: `make -n` (dry run)
  - **Ninja**: `ninja -n` (dry run)
  - **Unknown**: skip, return `succeeded=False` with a note in stderr
- Capture stdout and stderr fully.
- Treat timeout as failure with an actionable message.

### New dataclass: `BuildExplorationResult` in `models.py`

```python
@dataclass
class BuildExplorationResult:
    build_system: BuildSystem
    succeeded: bool
    command: list[str]
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
```

### `src/harnessbuddy/core/subprocesses.py`

Add a safe subprocess execution wrapper used by `explore()`: takes a command,
cwd, timeout; returns stdout, stderr, exit code, and duration. This is the
shared execution primitive for future tasks too.

### Integration with `_cmd_generate`

- When `--allow-host-build` is passed, call `explore()` after analysis and
  before generation.
- Pass `BuildExplorationResult` into `generate()` (update its signature if
  needed) so results appear in `provenance.json` under
  `"host_build_exploration"`.
- Print exploration outcome in the CLI summary: success or failure with a
  stderr excerpt on failure.
- Do not execute any build command when `--allow-host-build` is not passed.

## Acceptance Criteria

- `uv run harnessbuddy generate <local-fixture-path> --allow-host-build`
  attempts a configure step.
- Unit tests mock the subprocess; they do not run real build commands.
- Tests cover each build system's command construction.
- Tests cover the timeout path (mock a timed-out subprocess, assert
  `succeeded=False`).
- Tests prove exploration is skipped without `--allow-host-build`.
- `provenance.json` includes `"host_build_exploration"` when exploration ran,
  omits it otherwise.
- `uv run pytest -q`, `uv run ruff check`, and `uv run ty check` all pass.
