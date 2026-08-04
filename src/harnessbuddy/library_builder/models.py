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

    Frozen because every later phase reads it and none owns it: the packages the harness
    phase discovers travel to generation as an explicit argument, not as a mutation of
    this record.
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

    Both are expected results of asking an agent to attempt a repair, not errors: one
    needs a person to resolve something, the other needs time to pass. They are reported
    on the result so the pipeline has a single control path for "the build did not pass".
    """

    ACTION_REQUIRED = "action_required"
    BUDGET_LIMITED = "budget_limited"


@dataclass(kw_only=True)
class AgentOutcome:
    """What a repair-agent attempt contributes to a stage's result.

    Both stage results carry this same block — cost accounting, what the agent reported,
    and where its transcript is — so it is declared once here and inherited rather than
    repeated at each result type and each construction site. Keyword-only so the two
    results can still declare their own required fields.
    """

    llm_used: bool = False
    # The validated script to publish, set once a stage (or an agent's repair) has proven
    # it works and its paths are portable enough to copy verbatim.
    script_path: Path | None = None
    agent_stop_reason: AgentStopReason | None = None
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

    Exists so the script generators can be handed exactly the link inputs they use. The
    harness probe rewrites `compile_harness.sh` on every retry with one more resolved
    flag, and building a whole exploration result to carry four lists made the retry loop
    look like it was reporting an outcome when it was only proposing a link line.
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
    # The validated install tree, including when the analyzed source lives outside the
    # generated workspace and the build script therefore cannot be copied verbatim.
    install_dir: Path | None = None
    # Set when compile-commands capture succeeded for this build; None when the main
    # build failed (capture is never attempted) or capture was attempted and failed/
    # was skipped. Mutually exclusive with compile_commands_error.
    compile_commands_path: Path | None = None
    compile_commands_error: str | None = None


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

    @property
    def link_configuration(self) -> LinkConfiguration:
        """The link inputs this probe settled on, for regenerating compile_harness.sh."""
        return LinkConfiguration(
            static_libs=self.static_libs,
            transitive_link_flags=self.transitive_link_flags,
            extra_library_paths=self.extra_library_paths,
            extra_include_paths=self.extra_include_paths,
        )


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
