from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from harnessbuddy.library_builder.environments.base import Environment


class BuildSystem(Enum):
    CMAKE = "cmake"
    MESON = "meson"
    AUTOTOOLS = "autotools"
    MAKEFILE = "makefile"
    UNKNOWN = "unknown"


class AutotoolsSetup(Enum):
    CONFIGURE = "configure"  # configure script already present
    AUTOGEN = "autogen"  # autogen.sh present, must run before configure
    AUTORECONF = "autoreconf"  # only configure.ac / configure.in, need autoreconf -fiv


class Language(Enum):
    C = "c"
    CPP = "c++"
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
    autotools_setup: AutotoolsSetup | None = None
    system_packages: list[str] = field(default_factory=list)


@dataclass
class AgentReport:
    """Parsed contents of an agent invocation's agent_report.json, or "nothing reported"."""

    summary: str | None = None
    missing_libs: list[str] = field(default_factory=list)
    missing_apt_packages: list[str] = field(default_factory=list)
    missing_brew_packages: list[str] = field(default_factory=list)
    extra_include_paths: list[str] = field(default_factory=list)
    extra_library_paths: list[str] = field(default_factory=list)


@dataclass
class BuildExplorationResult:
    build_system: BuildSystem
    succeeded: bool
    command: list[str]
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    llm_used: bool = False
    # Set only when the source was cloned to the standard workdir/src layout, meaning
    # the script's paths are $SCRIPT_DIR-relative and it can be copied verbatim into
    # generated output directories (preserving any agent fixes).
    script_path: Path | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    transcript_path: Path | None = None
    agent_summary: str | None = None
    missing_apt_packages: list[str] = field(default_factory=list)
    missing_brew_packages: list[str] = field(default_factory=list)
    extra_include_paths: list[str] = field(default_factory=list)
    extra_library_paths: list[str] = field(default_factory=list)
    environment: Environment = Environment.LOCAL
    # Set when compile-commands capture succeeded for this build; None when the main
    # build failed (capture is never attempted) or capture was attempted and failed/
    # was skipped. Mutually exclusive with compile_commands_error.
    compile_commands_path: Path | None = None
    compile_commands_error: str | None = None


@dataclass
class HarnessExplorationResult:
    succeeded: bool
    command: list[str]
    static_libs: list[Path]
    include_dir: Path
    transitive_link_flags: list[str]
    stdout: str
    stderr: str
    exit_code: int
    missing_system_libs: list[str] = field(default_factory=list)
    llm_used: bool = False
    script_path: Path | None = None
    duration_seconds: float = 0.0
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    transcript_path: Path | None = None
    agent_summary: str | None = None
    missing_apt_packages: list[str] = field(default_factory=list)
    missing_brew_packages: list[str] = field(default_factory=list)
    extra_include_paths: list[str] = field(default_factory=list)
    extra_library_paths: list[str] = field(default_factory=list)
    environment: Environment = Environment.LOCAL


@dataclass
class BuildPaths:
    source_dir: str
    build_dir: str
    install_dir: str


@dataclass
class HarnessPaths:
    install_dir: Path
    workdir: Path


@dataclass
class GenerationResult:
    project_name: str
    output_path: Path
    files: list[Path]


class OutputDirectoryExistsError(Exception):
    """Target output directory already exists."""
