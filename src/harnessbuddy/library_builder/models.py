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
