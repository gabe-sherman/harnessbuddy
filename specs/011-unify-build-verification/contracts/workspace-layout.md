# Contract: `.harnessbuddy/<project>/` workspace layout during an oss-fuzz run

Describes what the workspace directory contains at each point in a `--environment oss-fuzz`
run, so a person (or the repair agent) inspecting it mid-run knows what to expect
(User Story 2, SC-004).

## After source ingestion, before the library-build stage

```
.harnessbuddy/<project>/
└── src/              # cloned repository (or bind-mounted external source path)
```

## After the library-build stage's first attempt (before/without an agent repair)

```
.harnessbuddy/<project>/
├── src/
├── build/
├── install/                    # lib/*.a, include/* once build_library.sh succeeds
├── project.yaml
├── Dockerfile                  # live variant: includes `bear` unconditionally (research.md #5)
├── build.sh
├── build_library.sh
├── compile_harnesses.sh        # stub — no-op, exits 0 (research.md #3)
├── harness_source/
│   └── default_fuzzer.c        # placeholder stub, written early so the atomic gate's
│                                # non-empty-/out check has something to find
└── compile_commands.json       # once capture succeeds (spec 010, unchanged)
```

`Dockerfile`/`build.sh`/`project.yaml` exist from this point on — this is what closes the
"only the final output looks like a real project" gap. `check_docker_build.sh
.harnessbuddy/<project> <project>` is buildable and runnable against this exact state.

## After harness-link discovery converges

```
.harnessbuddy/<project>/
├── ... (as above)
├── compile_harnesses.sh        # real content: STATIC_LIBS, EXTRA_LINK_FLAGS resolved
├── harness_source/
│   └── default_fuzzer.c|.cc    # discovery rewrites the extension in place if it finds the
│                                # link needs CXX — no separate probe file
└── out/                        # compiled harness binary once compile_harnesses.sh succeeds
```

## What final generation does with this directory

`generate_oss_fuzz(analysis, output_path, ...)` copies `project.yaml`,
`build.sh`, `build_library.sh`, `compile_harnesses.sh`, and `harness_source/` — including
its `default_fuzzer.{c,cc}`, whichever extension discovery settled on — verbatim from the
workspace into `output_path`. `write_default_fuzzer` only runs again as a fallback when
there was no validated workspace to copy from (e.g. an unknown build system). It calls
`oss_fuzz.workspace.write_dockerfile(output_path, analysis, include_bear=False)` to produce
the one file that intentionally differs from the workspace's live copy. `src/`, `build/`,
`install/`, `out/`, `state.json` are exploration-only and are never copied to `output_path`
— the shipped OSS-Fuzz project is exactly what a `git clone` of the real repository plus
these files would look like, per the OSS-Fuzz project convention. `compile_commands.json`
is copied too, but not into `output_path` itself — the CLI (`cli.py::_generate_outputs`)
copies it to `output_path.parent / "compile_commands.json"`, alongside the `local`/`oss-fuzz`
subdirs, so it's available for `extract-features` without keeping the exploration workspace
around or digging into `.harnessbuddy/<project>/`.

## Local environment (`--environment local`), for comparison

The local workspace layout is unchanged by this feature — it already matches
`local/generation.py`'s output shape (`build_library.sh`, `compile_harnesses.sh`,
`harness_src/`, `install/`, `out/`) using the same `$SCRIPT_DIR`-relative paths. The only
gap this feature closes for local is that `LocalExecutor` now calls
`check_local_build.sh` itself (contracts/verification-scripts.md) instead of re-implementing
the same `bash build_library.sh && bash compile_harnesses.sh` + artifact-check sequence
inline.
