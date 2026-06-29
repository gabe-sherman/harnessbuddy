from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from harnessbuddy.library_builder.models import AgentResult, AnalysisResult

_SKILL_PATH = (
    Path(__file__).parent.parent.parent.parent / "codex-skills" / "oss-fuzz-project" / "SKILL.md"
)
_DEFAULT_TIMEOUT = 600

_INLINE_INSTRUCTIONS = """\
Generate an OSS-Fuzz project in the output directory. Write these files:
Dockerfile, project.yaml, build.sh, build_library.sh, compile_harnesses.sh,
harness_source/default_fuzzer.cc, provenance.json.

Use gcr.io/oss-fuzz-base/base-builder as the base image. Clone the repo via
git clone in the Dockerfile. Build the library as a static library using the
detected build system. Write a build.env with HB_INCLUDE_FLAGS and
HB_LIBRARY_FLAGS. Compile harnesses with $CC/$CXX $LIB_FUZZING_ENGINE.
"""


def agent_generate(
    analysis: AnalysisResult,
    output_path: Path,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> AgentResult:
    """Spawn a Claude Code subprocess to generate OSS-Fuzz project files.

    The agent runs with cwd=analysis.source_path and writes files directly to
    output_path. Output streams to the terminal; no capture.
    """
    prompt = _build_prompt(analysis, output_path)
    start = time.monotonic()
    result = subprocess.run(
        ["claude", "--dangerously-skip-permissions", "-p", prompt],
        cwd=analysis.source_path,
        timeout=timeout,
    )
    duration = time.monotonic() - start
    files = _collect_files(output_path)
    if result.returncode == 0:
        _ensure_provenance(output_path, analysis)
        files = _collect_files(output_path)
    return AgentResult(
        succeeded=result.returncode == 0 and output_path.exists(),
        output_path=output_path,
        files=files,
        exit_code=result.returncode,
        duration_seconds=duration,
    )


def _build_prompt(analysis: AnalysisResult, output_path: Path) -> str:
    if _SKILL_PATH.exists():
        skill_ref = f"Read the skill at {_SKILL_PATH} and follow it."
    else:
        skill_ref = _INLINE_INSTRUCTIONS

    header_sample = ", ".join(
        str(h.relative_to(analysis.source_path))
        for h in analysis.headers[:5]
    )
    build_files = ", ".join(
        str(f.relative_to(analysis.source_path))
        for f in analysis.build_files
    )

    return (
        f"{skill_ref}\n\n"
        "Project context:\n"
        f"- project_name: {analysis.project_name}\n"
        f"- clone_url: {analysis.clone_url}\n"
        f"- repo_ref: {analysis.repo_ref or 'null'}\n"
        f"- build_system: {analysis.build_system.value}"
        "  (from static analysis — verify before trusting)\n"
        f"- language: {analysis.language.value}\n"
        f"- build_files: {build_files or 'none detected'}\n"
        f"- headers_sample: {header_sample or 'none detected'}\n\n"
        f"Output directory (pre-created, empty): {output_path}\n"
        "Write all OSS-Fuzz project files there directly."
    )


def _collect_files(output_path: Path) -> list[Path]:
    if not output_path.exists():
        return []
    return sorted(p for p in output_path.rglob("*") if p.is_file())


def _ensure_provenance(output_path: Path, analysis: AnalysisResult) -> None:
    provenance_path = output_path / "provenance.json"
    if provenance_path.exists():
        return
    data = {
        "project_name": analysis.project_name,
        "build_system": analysis.build_system.value,
        "clone_url": analysis.clone_url,
        "repo_ref": analysis.repo_ref,
        "generation_method": "agent",
        "warnings": analysis.warnings,
    }
    provenance_path.write_text(json.dumps(data, indent=2) + "\n")
