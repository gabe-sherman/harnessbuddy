"""Applying the shared build gate to a stage's probe result.

Both executors reach the same conclusion the same way, so the decision lives here once:
run the gate when the probe found something worth gating, and otherwise report the command
that would reproduce the failure without paying to rerun it.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from harnessbuddy.library_builder import workspace
from harnessbuddy.library_builder.build_parameters import (
    compile_commands_capture_environment,
    neutral_compiler_environment,
)
from harnessbuddy.library_builder.environments import verification
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import HarnessExplorationResult


def apply_to_harness_result(  # noqa: PLR0913 -- 4 keyword-only inputs, each independently meaningful
    harness_result: HarnessExplorationResult,
    workdir: Path,
    *,
    environment: Environment,
    project_name: str,
    library_llm_used: bool = False,
    bypass_scratch_validation: bool = False,
) -> HarnessExplorationResult:
    """Gate a harness probe's outcome with agents/scripts/check_build.sh.

    Skipped when the probe already failed or had nothing to link against: the gate would only
    reconfirm the same failure at the cost of a full rebuild. Its command is still reported, so
    the diagnostic says how to reproduce it.

    The gate rebuilds the library from nothing only when something changed since the last
    cold build, which is exactly when a repair agent produced this one. On the deterministic
    lane explore() already deleted build/ and install/ before building, so a rebuild here
    would repeat that build to reach the same state -- the run's single largest avoidable
    cost. bypass_scratch_validation drops the rebuild on the agent lane too, trading the
    from-scratch guarantee for speed at the caller's explicit request.
    """
    keep_artifacts = verification.gate_keeps_artifacts(
        library_llm_used=library_llm_used,
        bypass_scratch_validation=bypass_scratch_validation,
    )
    command = verification.verification_command(
        workdir,
        environment=environment,
        project_name=project_name,
        keep_artifacts=keep_artifacts,
    )
    if not harness_result.static_libs or not harness_result.succeeded:
        return dataclasses.replace(
            harness_result, command=command, gate_keeps_artifacts=keep_artifacts
        )

    # The gate builds the library and compiles harnesses in one invocation, so no stage's
    # compiler environment may be in effect: each generated script bakes in its own.
    with neutral_compiler_environment(), compile_commands_capture_environment():
        result = verification.run_verification(
            workdir,
            environment=environment,
            project_name=project_name,
            keep_artifacts=keep_artifacts,
        )
    _localize_compile_commands(workdir, environment=environment)
    return dataclasses.replace(
        harness_result,
        succeeded=result.passed,
        command=result.command,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=0 if result.passed else 1,
        duration_seconds=result.duration_seconds,
        gate_keeps_artifacts=keep_artifacts,
    )


def _localize_compile_commands(workdir: Path, *, environment: Environment) -> None:
    """Rewrite a container-built capture's /src paths back to the host workspace.

    Done here because here is where the container build happens, so every lane -- deterministic
    or agent-repaired -- gets the same treatment. Keyed to the library-build result instead, it
    was skipped whenever a repair agent produced the build, publishing container paths.
    """
    if environment is not Environment.OSS_FUZZ:
        return
    # Imported here: oss_fuzz imports this module, so a module-level import would be circular.
    from harnessbuddy.library_builder.environments.oss_fuzz import rewrite_compile_commands_paths

    captured = workspace.find_compile_commands(workdir)
    if captured is not None:
        rewrite_compile_commands_paths(captured, workdir)
