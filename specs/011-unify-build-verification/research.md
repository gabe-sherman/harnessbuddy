# Phase 0 Research: Unified Build Verification

## 1. Materializing the real OSS-Fuzz project in the workspace, early

**Decision**: As soon as the library-build stage knows its build system, autotools setup,
and accumulated apt packages, write the real `Dockerfile` (with `RUN git clone
<clone_url> $SRC/src` and, if `repo_ref` is set, `RUN git -C $SRC/src checkout
<repo_ref>`), `build.sh`, and `project.yaml` directly into `.harnessbuddy/<project>/`
(the workspace) — the same directory `build_library.sh`/`compile_harnesses.sh` already
live in. `harness_source/` (already the directory name `harness_explorer.py` uses for the
oss-fuzz environment, per today's `harness_dir_name = "harness_source" if oss_fuzz else
"harness_src"`) is created at the same time. This is the same content
`oss_fuzz/generation.py`'s `_write_project_yaml`/`_write_dockerfile`/`_write_build_sh`
already produce — the new module (`oss_fuzz/workspace.py`) hosts the writer functions once,
called both here (early) and by final generation (selecting which files to copy).

**Rationale**: This is what closes the concrete gap found while planning: `cli.py` already
computes `oss_output_path` and threads it into `agents.py::_verification_command` as
`oss_fuzz_project_dir` *during exploration*, but `generate_oss_fuzz()` — the only thing that
creates that directory — doesn't run until after exploration finishes. An agent told to run
`check_docker_build.sh <oss_output_path> <project_name>` mid-run today would fail on a
missing directory. Making the workspace itself the real project directory (and pointing
`oss_fuzz_project_dir` at the workspace during exploration, not a not-yet-created future
path) fixes this by construction, and is exactly the "layout exists during the build/testing
process too" the feature requires (User Story 2).

**Alternatives considered**:
- *Keep a separate synthetic probe image, only assemble the real project at the very end*
  (today's behavior): rejected — this is the disjointedness the feature exists to remove,
  and leaves the dangling-path bug in place.
- *Materialize the real project directory somewhere other than the existing
  `.harnessbuddy/<project>/` workspace* (e.g. a new `.harnessbuddy/<project>/oss-fuzz/`
  subdirectory, keeping `build_library.sh` etc. at the top level as today): rejected as an
  unnecessary extra layer — `build_library.sh`/`compile_harnesses.sh`/`harness_source/`
  already live directly in the workspace; nesting them under an `oss-fuzz/` subdirectory
  would require rewriting every existing path convention (`$SCRIPT_DIR`-relative paths,
  `is_standard_source_layout`) for no benefit, since the workspace is already
  environment-specific per run (a run only ever uses one environment).

## 2. Keeping harness-dependency discovery fast without bypassing the shared script

**Decision**: The library-build stage's image (built via `docker build` against the real
workspace `Dockerfile`, once apt packages/build system are known) is reused directly for
`harness_explorer.py`'s internal retry loop (up to 5 attempts) via the existing
`_docker_run_factory`-style direct `docker run --entrypoint bash <image> -c "bash
compile_harnesses.sh"` mechanism — no `docker build`, no `compile` entrypoint, per attempt.
Once a stage's script converges (or discovery exhausts its attempts), the pipeline's actual
pass/fail signal for that stage comes from running the shared script
(`agents/scripts/check_docker_build.sh <workspace> <project_name>` — `docker build` +
`docker run --entrypoint bash <tag> -c "compile && ..."`) exactly once, via `subprocess`,
from `OssFuzzExecutor`.

**Rationale**: The Clarifications session confirmed collapsing to one atomic check
(build the container, run `compile`) as the target end state (per FR-002), but 5 rebuilds of
a self-contained image (`git clone` + `apt-get install` layers) per harness-link discovery
attempt would be markedly slower than today's direct-exec-against-an-already-built-image
loop, for no correctness benefit — discovery is internal probing to find the right link
flags, not the verification gate itself (spec.md FR-011, Assumptions). Docker layer caching
means the *final* atomic `check_docker_build.sh` call after discovery converges is itself
fast (only the last `COPY compile_harnesses.sh`/`COPY harness_source` layers rebuild), so
the one atomic check that actually gates the pipeline is not the slow path.

**Alternatives considered**:
- *Route every discovery attempt through `check_docker_build.sh`/`compile`*: rejected —
  the user's own Clarifications answer distinguishes "the pass/fail signal" (must be the
  shared script) from "internal iteration" (may stay fast/direct), and forcing every retry
  through a full rebuild would be a real performance regression with no observable benefit
  (discovery output is never shown to the user as "the" result; only the final atomic
  check's result is).
- *Drop the discovery loop's granularity entirely, relying on `compile`'s combined output to
  guess link flags*: rejected — `compile`'s output through the atomic entrypoint is exactly
  the same linker-error text `harness_explorer.py` already parses; there's no information
  gain from switching, only a much slower feedback loop per attempt.

## 3. Library-build stage verification during exploration

**Decision**: The library-build stage's own pass/fail check (needed to decide whether to
invoke the library-build repair agent, before harness-link discovery even starts) is also
the shared script, `check_docker_build.sh`, run once the workspace's `Dockerfile`/`build.sh`
exist — even though at that point `compile_harnesses.sh` is still a stub
(`_COMPILE_HARNESSES_SH_STUB`) that does nothing. Its stub form doesn't fail `compile`
(it's a no-op script that exits 0), so the atomic check's result at this point reflects only
`build_library.sh`'s outcome. Once harness-link discovery finishes and
`compile_harnesses.sh` has real content, a second (Docker-layer-cached, thus fast) call to
`check_docker_build.sh` produces the final, complete atomic result covering both stages.

**Rationale**: This keeps "the shared script is the only place that decides pass/fail"
(FR-001) true for *both* stages, without inventing a second verification mechanism just for
the library-build stage. The stub `compile_harnesses.sh` behaving as a no-op is existing
behavior (`_COMPILE_HARNESSES_SH_STUB` already), not something new introduced by this
change.

**Alternatives considered**:
- *Run `bash build_library.sh` directly (not through `compile`) for the library-only check*:
  rejected — this is exactly the per-stage ad hoc mechanism the feature removes; keeping it
  around "just for this one stage" would preserve half the disjointedness this feature
  exists to close.

## 4. Docker layer caching keeps repeated atomic checks affordable

**Decision**: Keep the workspace `Dockerfile`'s instruction order stable across the run:
`FROM base-builder` → `RUN apt-get install` (accumulated packages, including the
unconditional `bear`, per spec 010) → `RUN git clone`/`checkout` → `COPY harness_source` →
`COPY build.sh build_library.sh compile_harnesses.sh` → `WORKDIR $SRC/src` — matching
`oss_fuzz/generation.py::_write_dockerfile`'s existing layer order. Only rewrite the
`Dockerfile` itself (invalidating the `apt-get`/`git clone` layer cache) when the apt-package
set changes (e.g. an agent reports new packages), same trigger `_ensure_probe_image` already
uses today (`self._built_apt_packages == packages` check).

**Rationale**: Ordering cheap-to-invalidate instructions (`COPY` of small, frequently-edited
scripts) after expensive-to-invalidate ones (`apt-get`, `git clone`) is standard Docker
layer-caching practice and is already how `_write_dockerfile` orders its `RUN`/`COPY` lines
— no new technique, just reusing it earlier (during exploration) instead of only at final
generation.

**Alternatives considered**: None seriously — this is standard Docker practice already
reflected in existing code; the only decision was confirming the existing instruction order
already caches well, which it does.

## 5. compile_commands.json capture stays exactly as planned in spec 010

**Decision**: No change to `exploration.py`'s `_build_command`/`_capture_compile_commands`
or `oss_fuzz.py`'s bear-provisioning behavior. The workspace's "live" `Dockerfile`
unconditionally includes `bear` (matching `_ensure_probe_image`'s current
`all_packages = " ".join(("bear", *packages))`); `oss_fuzz/generation.py::_write_dockerfile`
continues to exclude it from the file copied to the final `oss-fuzz/` output. `bear --
bash build_library.sh` (or the CMake/Meson equivalents) still runs as today, just inside
the real workspace image instead of the synthetic probe image — nothing about *how*
compile_commands.json is captured changes, only *which* image the build runs inside.

**Rationale**: Confirmed directly in this feature's Clarifications — preserve as-is. The
only touch point is that `bear`'s presence must survive the switch from probe image to real
workspace image, which it does as long as the workspace `Dockerfile`'s apt-package list keeps
including `bear` unconditionally (a one-line carry-over, not a design change).

**Alternatives considered**: N/A — explicitly out of scope per the Clarifications answer.

## 6. Final generation copies validated files instead of re-deriving them

**Decision**: `generate_oss_fuzz()`/`generate_local()` stop writing `Dockerfile`,
`build.sh`, `build_library.sh`, `compile_harnesses.sh`, `project.yaml`, and
`harness_source/*` from templates or from `BuildExplorationResult`/`HarnessExplorationResult`
fields. Instead, they copy those files directly from the now-already-real workspace
(`shutil.copy2`/`copytree`, mirroring the existing `script_path`-copy-verbatim pattern in
`_write_build_library_sh`/`_write_compile_harnesses_sh`, just extended to every file instead
of two). `local/generation.py` additionally writes `setup.sh` (a genuinely output-only
bootstrap file that has no workspace equivalent, since exploration already operates on a
pre-cloned repository). The bear-stripped `Dockerfile` variant (Research #5) is the one
exception that is still derived rather than copied verbatim, by construction (it must differ
from the workspace's live version by exactly the `bear` package).

**Rationale**: Directly satisfies FR-005 ("final generated output MUST be the same project
directory that was validated during the run ... MUST NOT re-derive or re-template build
artifacts that verification already produced and validated"). It also deletes code: the
template-based fallback branches in `_write_build_library_sh`/`_write_compile_harnesses_sh`
existed specifically to handle "no exploration was run, or it ran in a different
environment" — with the workspace *being* the environment-specific project directory
throughout, that branch's only remaining case is "exploration never ran at all"
(`--skip-validation` with no prior run), which still needs a fallback template, but the
common case collapses to a plain copy.

**Alternatives considered**:
- *Keep re-deriving final output from typed result fields, just make the fields richer*:
  rejected — this is strictly more code (richer fields, still two writer implementations)
  for a worse guarantee (byte-for-byte equality with what was validated is implicit, not
  structural) than "copy the file that was already validated."

## 7. `oss_fuzz_project_dir` in agent prompts simplifies to "the workspace"

**Decision**: `agents.py::_verification_command`'s `oss_fuzz_project_dir` parameter, and its
callers in `cli.py` (`build_library`/`build_harness`, which currently pass the not-yet-created
`oss_output_path`), are updated to pass the workspace path instead — during exploration, the
workspace *is* the oss-fuzz project directory (Research #1), so there is no longer a
separate "eventual" path to reference.

**Rationale**: Removes the exact dangling-path gap identified in this plan's Summary.
Since the workspace and the "oss-fuzz project directory" are now the same directory for the
duration of exploration, carrying a second path through `build_library`/`build_harness`/
`_run_library_phase`/`_run_harness_phase` no longer reflects anything distinct — it can be
dropped once workspace materialization (Research #1) lands, simplifying those call sites.

**Alternatives considered**: *Keep `oss_output_path` as a distinct parameter for
forward-compatibility, populated with the same value as `workspace`*: rejected per
Constitution Principle V — no known future need for the two paths to diverge, and an unused
distinction is exactly the kind of speculative surface the project avoids.
