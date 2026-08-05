from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path

from harnessbuddy.core.subprocesses import MergedOutput
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
    BOOTSTRAP = "bootstrap"  # bootstrap present (gnulib convention), same role as autogen.sh
    AUTORECONF = "autoreconf"  # only configure.ac / configure.in, need autoreconf -fiv


class Language(Enum):
    C = "c"
    CPP = "c++"


@dataclass(frozen=True)
class AnalysisResult:
    """What static analysis concluded about a repository — immutable pipeline input.

    Frozen because every later phase reads it and none owns it. Anything a later phase
    discovers travels onward as an explicit argument, not as a mutation of this record.
    """

    project_name: str
    source_path: Path
    build_system: BuildSystem
    language: Language
    clone_url: str
    repo_ref: str | None
    warnings: list[str] = field(default_factory=list)
    autotools_setup: AutotoolsSetup | None = None


@dataclass
class AgentReport:
    """Parsed contents of an agent invocation's agent_report.json, or "nothing reported"."""

    summary: str | None = None
    missing_libs: list[str] = field(default_factory=list)
    missing_apt_packages: list[str] = field(default_factory=list)
    extra_include_paths: list[str] = field(default_factory=list)
    extra_library_paths: list[str] = field(default_factory=list)


class AgentStopReason(StrEnum):
    """Why a repair agent stopped without fixing the build.

    Both are expected outcomes rather than errors: one needs a person, the other needs time
    to pass. Reported on the result, so "the build did not pass" stays one control path.
    """

    ACTION_REQUIRED = "action_required"
    BUDGET_LIMITED = "budget_limited"


@dataclass(kw_only=True)
class AgentOutcome:
    """What a repair-agent attempt contributes to a stage's result.

    Both stage results carry this same block, so it is declared once here and inherited.
    Keyword-only, so the two results can still declare their own required fields.
    """

    llm_used: bool = False
    # The validated script to publish, set once a stage (or an agent's repair) proved it
    # works and its paths are portable enough to copy verbatim.
    script_path: Path | None = None
    agent_stop_reason: AgentStopReason | None = None
    # Non-empty only when HarnessBuddy rejected a repair the agent reported as done, because
    # the artifacts it claims to have produced are not there. That is a different thing to
    # tell the user than an agent that failed and said so.
    validation_errors: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    transcript_path: Path | None = None
    agent_summary: str | None = None
    missing_apt_packages: list[str] = field(default_factory=list)
    extra_include_paths: list[str] = field(default_factory=list)
    extra_library_paths: list[str] = field(default_factory=list)
    environment: Environment = Environment.LOCAL


@dataclass
class LinkConfiguration:
    """Everything a harness link line needs: the library's own archives, plus what they
    transitively pull in and where to find it.

    Separate from HarnessExplorationResult so the retry loop, which rewrites
    compile_harness.sh with one more resolved flag per attempt, proposes a link line rather
    than appearing to report an outcome.
    """

    static_libs: list[Path] = field(default_factory=list)
    transitive_link_flags: list[str] = field(default_factory=list)
    extra_library_paths: list[str] = field(default_factory=list)
    extra_include_paths: list[str] = field(default_factory=list)


@dataclass
class BuildExplorationResult(MergedOutput, AgentOutcome):
    build_system: BuildSystem
    succeeded: bool
    command: list[str]
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    # The validated install tree. Set even when the source lives outside the workspace and
    # the build script therefore cannot be copied verbatim.
    install_dir: Path | None = None


@dataclass
class HarnessExplorationResult(MergedOutput, AgentOutcome):
    succeeded: bool
    command: list[str]
    static_libs: list[Path]
    include_dir: Path
    transitive_link_flags: list[str]
    stdout: str
    stderr: str
    exit_code: int
    missing_system_libs: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    # Whether the gate that produced this result reused the workspace's install/ rather than
    # rebuilding the library from nothing. Stamped by environments/gate.py, so a repair agent
    # is told to run the same gate the pipeline ran instead of recomputing the decision and
    # paying for a cold rebuild the pipeline had decided to skip. See
    # verification.gate_keeps_artifacts.
    gate_keeps_artifacts: bool = False


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
