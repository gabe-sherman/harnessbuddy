from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class BuildSystem(Enum):
    CMAKE = "cmake"
    MESON = "meson"
    AUTOTOOLS = "autotools"
    MAKEFILE = "makefile"
    NINJA = "ninja"
    UNKNOWN = "unknown"


class Language(Enum):
    C = "c"
    CPP = "cpp"
    C_AND_CPP = "c_and_cpp"
    UNKNOWN = "unknown"


@dataclass
class AnalysisResult:
    project_name: str
    source_path: Path
    build_system: BuildSystem
    build_files: list[Path]
    headers: list[Path]
    language: Language
    clone_url: str
    repo_ref: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class BuildExplorationResult:
    build_system: BuildSystem
    succeeded: bool
    command: list[str]
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float


@dataclass
class GenerationResult:
    project_name: str
    output_path: Path
    files: list[Path]


@dataclass
class AgentResult:
    succeeded: bool
    output_path: Path
    files: list[Path]
    exit_code: int
    duration_seconds: float


@dataclass
class SandboxResult:
    succeeded: bool
    skipped: bool
    skip_reason: str
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
