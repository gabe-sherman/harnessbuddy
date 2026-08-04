"""Applying the shared build gate to a stage's probe result.

Both executors reach the same conclusion the same way, so the decision lives here once:
run the gate when the probe found something worth gating, and otherwise report the command
that would reproduce the failure without paying to rerun it.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from harnessbuddy.library_builder.build_parameters import neutral_compiler_environment
from harnessbuddy.library_builder.environments import verification
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import HarnessExplorationResult


def apply_to_harness_result(
    harness_result: HarnessExplorationResult,
    workdir: Path,
    *,
    environment: Environment,
    project_name: str,
) -> HarnessExplorationResult:
    """Gate a harness probe's outcome with agents/scripts/check_build.sh.

    Skipped when the probe already failed or had nothing to link against: the gate would only
    reconfirm the same failure at the cost of a full rebuild. Its command is still reported, so
    the diagnostic says how to reproduce it.
    """
    command = verification.verification_command(
        workdir, environment=environment, project_name=project_name
    )
    if not harness_result.static_libs or not harness_result.succeeded:
        return dataclasses.replace(harness_result, command=command)

    # The gate builds the library and compiles harnesses in one invocation, so no stage's
    # compiler environment may be in effect: each generated script bakes in its own.
    with neutral_compiler_environment():
        result = verification.run_verification(
            workdir, environment=environment, project_name=project_name
        )
    return dataclasses.replace(
        harness_result,
        succeeded=result.passed,
        command=result.command,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=0 if result.passed else 1,
        duration_seconds=result.duration_seconds,
    )
