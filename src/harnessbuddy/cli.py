from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harnessbuddy.library_builder.models import AnalysisResult, BuildExplorationResult


def _configure_generate_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "repo_url",
        metavar="REPO_URL",
        help="Repository URL or local path to analyze.",
    )
    p.add_argument(
        "--agent",
        choices=["codex", "claude"],
        default=None,
        metavar="codex|claude",
        help="Agent backend for fallback. Overridden by --no-agents.",
    )
    p.add_argument(
        "--output",
        metavar="DIR",
        help="Parent directory for the generated project. Output is written to DIR/PROJECT_NAME.",
    )
    p.add_argument(
        "--project-name",
        metavar="NAME",
        help="Override the project name inferred from REPO_URL.",
    )
    p.add_argument(
        "--repo-ref",
        metavar="REF",
        help="Branch, tag, or commit to check out in the generated Dockerfile.",
    )
    p.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip oss-fuzz validation after project generation.",
    )
    p.add_argument(
        "--no-agents",
        action="store_true",
        help="Disable all agent fallback regardless of --agent.",
    )
    p.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Keep the working directory after the run.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harnessbuddy",
        description="Prepare C/C++ libraries for OSS-Fuzz project generation.",
    )
    subparsers = parser.add_subparsers(dest="command")
    generate = subparsers.add_parser(
        "generate",
        help="Generate an oss-fuzz project for a C/C++ library.",
        description="Generate an oss-fuzz project directory for a C/C++ library repository.",
    )
    _configure_generate_parser(generate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "generate":
        return _cmd_generate(args)
    return 0


def load_system_deps(analysis: AnalysisResult) -> None:
    """Populate analysis.system_packages from system_deps.json written by a prior agent run.

    system_deps.json lives in the source directory and is written by the library builder
    agent when it identifies required apt packages. Loading it here means subsequent runs
    can embed the packages into the generated Dockerfile and setup.sh without re-running
    the agent.
    """
    deps_file = analysis.source_path / "system_deps.json"
    if not deps_file.exists():
        return
    try:
        data = json.loads(deps_file.read_text())
        packages = data.get("apt_packages", [])
        if isinstance(packages, list):
            analysis.system_packages = [str(p) for p in packages]
    except (json.JSONDecodeError, OSError):
        pass


def build_library(
    analysis: AnalysisResult,
    workspace: Path,
    *,
    agent: str | None = None,
    timeout: int = 300,
) -> BuildExplorationResult:
    """Run explore, then optionally fall back to an LLM agent if the build fails.

    Returns the final BuildExplorationResult. result.llm_used is True when the
    agent path was taken.
    """
    from harnessbuddy.library_builder.exploration import explore

    result = explore(analysis, workspace, timeout=timeout)
    if not result.succeeded and agent is not None:
        from harnessbuddy.library_builder.agents import invoke_library_builder_agent
        result = invoke_library_builder_agent(analysis, result, workspace, tool=agent)
    return result


def _cmd_generate(args: argparse.Namespace) -> int:
    from harnessbuddy.core.paths import default_state_dir, project_dir
    from harnessbuddy.core.repos import (
        NoCloneableOriginError,
        RepositoryNotFoundError,
        ingest_local,
        ingest_url,
    )
    from harnessbuddy.library_builder.analysis import UnsupportedRepositoryError, analyze

    output_parent = Path(args.output) if args.output else Path.cwd()
    state_dir = default_state_dir()

    try:
        if _is_url(args.repo_url):
            source = ingest_url(
                args.repo_url,
                project_name=args.project_name,
                repo_ref=args.repo_ref,
                state_dir=state_dir,
            )
        else:
            source = ingest_local(
                Path(args.repo_url),
                project_name=args.project_name,
                repo_ref=args.repo_ref,
            )
    except RepositoryNotFoundError as exc:
        print(f"Repository not found: {exc}", file=sys.stderr)
        return 1
    except NoCloneableOriginError:
        print(
            "No cloneable git origin found. Provide a URL instead of a local path,"
            " or add a remote origin.",
            file=sys.stderr,
        )
        return 1

    try:
        analysis = analyze(source)
    except UnsupportedRepositoryError:
        print("No C/C++ build signals found in this repository.", file=sys.stderr)
        return 1

    workspace = project_dir(state_dir, analysis.project_name)
    load_system_deps(analysis)
    agent = None if args.no_agents else args.agent
    print(f"Running host build in {workspace} ...")
    if agent:
        print(f"Agent fallback enabled ({agent}).")

    result = build_library(analysis, workspace, agent=agent)

    if result.llm_used:
        print("Agent finished.")

    if not result.succeeded:
        print(f"Failed to produce valid build: {result.stdout}", file=sys.stderr)
        return 1

    from harnessbuddy.library_builder.local.generation import generate_local
    from harnessbuddy.library_builder.models import OutputDirectoryExistsError
    from harnessbuddy.library_builder.oss_fuzz.generation import generate_oss_fuzz

    try:
        local_result = generate_local(analysis, output_parent, result)
        oss_fuzz_result = generate_oss_fuzz(analysis, output_parent, result)
    except OutputDirectoryExistsError as exc:
        print(f"Output directory already exists: {exc}", file=sys.stderr)
        return 1

    print(f"Local build:  {local_result.output_path}")
    print(f"OSS-Fuzz:     {oss_fuzz_result.output_path}")

    return 0


def _is_url(value: str) -> bool:
    return value.startswith(("https://", "http://", "git://", "ssh://", "git@"))
