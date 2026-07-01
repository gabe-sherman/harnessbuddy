# HarnessBuddy Agent Guide

## Project Summary

HarnessBuddy is a developer tool for preparing software libraries to be built,
harnessed, and eventually fuzzed. The project should reduce the setup work
required to turn an upstream open-source repository into a reproducible fuzzing
project.

The first product goal is:

1. Accept a user-provided repository URL.
2. Inspect the repository enough to understand its language, build system, and
   likely library targets.
3. Generate an oss-fuzz-compatible project directory.
4. Include at minimum a `Dockerfile` and `build.sh` that can build the target
   library inside the oss-fuzz environment.

## Current State

This repository is at the initial scaffolding stage. Prefer simple, explicit
code and documentation until the project has real workflows to generalize from.

## Plans

Scoped implementation plans live in `plans/`. Check `plans/README.md` before
starting feature work, then follow the plan that matches the requested task.

## Project-Local Skills

Reusable Codex skills live in `codex-skills/`. When the user invokes `$dev`,
`$implement`, `$review`, or `$test` in this repository and that skill is not already
available globally, read the matching `codex-skills/<name>/SKILL.md` and follow it as
the active workflow.

## Expected Workflow

The core workflow should become:

1. Ingest a repo URL and clone or fetch enough metadata to analyze it.
2. Detect build and package signals, such as language, package manager, native
   build files, test commands, and existing fuzz targets.
3. Produce an oss-fuzz project layout for the target library.
4. Validate the generated `Dockerfile` and `build.sh` by running the oss-fuzz
   build flow locally where possible.
5. Report what was generated, what assumptions were made, and what still needs
   human attention.

## Design Principles

- Start with the smallest reliable path for one repository and expand from
  concrete cases.
- Prefer deterministic project analysis over LLM-only guessing.
- Keep generated files readable and editable by humans.
- Preserve provenance: generated outputs should explain which repo signals led
  to each build decision.
- Treat cloned third-party repositories as untrusted input. Do not execute their
  scripts without sandboxing or explicit user approval.
- Validate generated oss-fuzz projects with real build commands before claiming
  success.

## Initial Implementation Notes

- Model repository analysis separately from file generation.
- Keep oss-fuzz output generation focused on project files first:
  `project.yaml`, `Dockerfile`, and `build.sh`.
- Avoid adding support for many languages at once. Add one ecosystem only when
  there is a test fixture or real repository that exercises it.
- Prefer fixtures that are tiny local repositories over network-dependent tests.
- Generated shell scripts must use `set -euo pipefail`.

## Development Standards

- Keep functions small and behavior-focused.
- Add tests for error paths, unsupported repositories, and malformed input.
- Use structured parsers for known files when practical instead of regular
  expressions over entire files.
- Do not add dependencies until they are needed by a concrete workflow.
- Document implemented behavior only. Do not describe future capabilities as if
  they already work.

## Open Questions

- Which language ecosystem should be supported first?
- Should repository analysis run fully locally, in a container, or through a
  remote worker?
- Should generated oss-fuzz projects be written to disk, returned as an archive,
  opened as a pull request, or all of these behind explicit commands?
- How much harness generation should happen in the first milestone versus only
  producing build scaffolding?

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
