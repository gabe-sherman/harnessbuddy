from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AgentResult,
    AnalysisResult,
    BuildExplorationResult,
    GenerationResult,
    SandboxResult,
)


def _configure_generate_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "repo_url",
        metavar="REPO_URL",
        help="Repository URL or local path to analyze.",
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
        "--agent",
        choices=["auto", "codex", "claude"],
        default="auto",
        metavar="auto|codex|claude",
        help="Agent backend for fallback (default: auto). Overridden by --no-agents.",
    )
    p.add_argument(
        "--allow-host-build",
        action="store_true",
        help="Allow host-side build exploration (only applies with --no-agents).",
    )
    p.add_argument(
        "--sandbox-test",
        action="store_true",
        help="Run docker build on the generated project to validate it.",
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


def _cmd_generate(args: argparse.Namespace) -> int:
    from harnessbuddy.core.paths import default_state_dir
    from harnessbuddy.core.repos import (
        NoCloneableOriginError,
        RepositoryNotFoundError,
        ingest_local,
        ingest_url,
    )
    from harnessbuddy.library_builder.analysis import UnsupportedRepositoryError, analyze

    output_parent = Path(args.output) if args.output else Path.cwd()

    try:
        if _is_url(args.repo_url):
            source = ingest_url(
                args.repo_url,
                project_name=args.project_name,
                repo_ref=args.repo_ref,
                state_dir=default_state_dir(),
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

    if args.no_agents:
        return _run_static(args, analysis, output_parent)

    output_path = output_parent / analysis.project_name
    if output_path.exists():
        print(f"Output directory already exists: {output_path}", file=sys.stderr)
        return 1
    output_path.mkdir(parents=True)

    from harnessbuddy.library_builder.agents import agent_generate

    agent_result = agent_generate(analysis, output_path)
    _print_agent_summary(agent_result, analysis)

    if not agent_result.succeeded:
        return 1

    if args.sandbox_test:
        from harnessbuddy.library_builder.sandbox import sandbox_test

        sandbox = sandbox_test(output_path)
        _print_sandbox_summary(sandbox)
        if not sandbox.succeeded and not sandbox.skipped:
            return 1

    return 0


def _run_static(
    args: argparse.Namespace, analysis: AnalysisResult, output_parent: Path
) -> int:
    import tempfile

    from harnessbuddy.library_builder.generation import OutputDirectoryExistsError, generate

    exploration: BuildExplorationResult | None = None
    if args.allow_host_build:
        from harnessbuddy.library_builder.exploration import explore

        with tempfile.TemporaryDirectory() as _workdir:
            exploration = explore(analysis, Path(_workdir))

    try:
        result = generate(analysis, output_parent, exploration)
    except OutputDirectoryExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _print_static_summary(result, analysis, exploration)
    return 0


def _print_static_summary(
    result: GenerationResult,
    analysis: AnalysisResult,
    exploration: BuildExplorationResult | None,
) -> None:
    print(f"Generated oss-fuzz project: {result.output_path}")
    print(f"  Project name:  {result.project_name}")
    print(f"  Build system:  {analysis.build_system.value}")
    print(f"  Language:      {analysis.language.value}")
    if exploration is not None:
        status = "succeeded" if exploration.succeeded else "failed"
        print(f"  Host build exploration: {status}")
    for warning in analysis.warnings:
        print(f"  Warning: {warning}")


def _print_agent_summary(agent_result: AgentResult, analysis: AnalysisResult) -> None:
    status = "succeeded" if agent_result.succeeded else "failed"
    print(f"Generated oss-fuzz project (agent): {agent_result.output_path}  [{status}]")
    print(f"  Project name:  {analysis.project_name}")
    print(f"  Build system:  {analysis.build_system.value}")
    print(f"  Language:      {analysis.language.value}")
    print(f"  Files written: {len(agent_result.files)}")
    print(f"  Duration:      {agent_result.duration_seconds:.1f}s")
    for warning in analysis.warnings:
        print(f"  Warning: {warning}")


def _print_sandbox_summary(sandbox: SandboxResult) -> None:
    if sandbox.skipped:
        print(f"  Sandbox test:  skipped ({sandbox.skip_reason})")
    elif sandbox.succeeded:
        print("  Sandbox test:  passed")
    else:
        print("  Sandbox test:  failed")
        if sandbox.stderr:
            print("  Docker stderr (last 20 lines):")
            for line in sandbox.stderr.splitlines()[-20:]:
                print(f"    {line}")


def _is_url(value: str) -> bool:
    return value.startswith(("https://", "http://", "git://", "ssh://", "git@"))
