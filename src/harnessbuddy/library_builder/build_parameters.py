"""Compiler settings used while preparing and emitting a local fuzzing build."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

_DEFAULT_HARNESS_FLAGS = "-fsanitize=fuzzer"


@dataclass(frozen=True)
class BuildParameters:
    """Separate compiler settings for library and final-harness compilation."""

    cc: str
    cxx: str
    library_cflags: str
    library_cxxflags: str
    harness_cflags: str
    harness_cxxflags: str
    # Build-system-level configure options: cmake cache variables, meson options, autotools
    # --enable-* switches, make variables. Baked into the generated script rather than passed
    # through the environment, since cmake and meson have no environment equivalent.
    library_configure_args: tuple[str, ...] = ()

    @classmethod
    def from_args(cls, args: object) -> BuildParameters:
        """Resolve CLI arguments against the caller's existing compiler environment."""
        return cls(
            cc=_argument_or_environment(args, "cc", "CC", "clang"),
            cxx=_argument_or_environment(args, "cxx", "CXX", "clang++"),
            library_cflags=_argument_or_environment(args, "library_cflags", "CFLAGS", ""),
            library_cxxflags=_argument_or_environment(args, "library_cxxflags", "CXXFLAGS", ""),
            harness_cflags=_argument_or_default(args, "harness_cflags", _DEFAULT_HARNESS_FLAGS),
            harness_cxxflags=_argument_or_default(args, "harness_cxxflags", _DEFAULT_HARNESS_FLAGS),
            library_configure_args=_repeated_argument(args, "library_configure_args"),
        )

    @classmethod
    def defaults(cls) -> BuildParameters:
        """The settings a run with no CLI arguments would use.

        For a caller that builds outside a `generate` invocation and still needs the compiler
        resolution rules the CLI applies.
        """
        return cls.from_args(_NO_ARGUMENTS)

    @contextmanager
    def library_environment(self) -> Iterator[None]:
        """Expose library settings to build commands and repair agents."""
        with _temporary_environment(
            {
                "CC": self.cc,
                "CXX": self.cxx,
                "CFLAGS": self.library_cflags,
                "CXXFLAGS": self.library_cxxflags,
            }
        ):
            yield

    @contextmanager
    def harness_environment(self) -> Iterator[None]:
        """Expose final-harness settings while validating and emitting its compiler."""
        with _temporary_environment(
            {
                "CC": self.cc,
                "CXX": self.cxx,
                "CFLAGS": self.harness_cflags,
                "CXXFLAGS": self.harness_cxxflags,
            }
        ):
            yield

    def to_dict(self) -> dict[str, str | list[str]]:
        """Return the effective settings suitable for published run metadata."""
        return {
            "cc": self.cc,
            "cxx": self.cxx,
            "library_cflags": self.library_cflags,
            "library_cxxflags": self.library_cxxflags,
            "harness_cflags": self.harness_cflags,
            "harness_cxxflags": self.harness_cxxflags,
            "library_configure_args": list(self.library_configure_args),
        }


# A sentinel with no attributes, so every getattr in from_args misses and each field falls
# back to the environment-or-default rules.
_NO_ARGUMENTS = object()


def _argument_or_environment(args: object, argument: str, environment: str, default: str) -> str:
    value = getattr(args, argument, None)
    return value if isinstance(value, str) else os.environ.get(environment, default)


def _argument_or_default(args: object, argument: str, default: str) -> str:
    value = getattr(args, argument, None)
    return value if isinstance(value, str) else default


def _repeated_argument(args: object, argument: str) -> tuple[str, ...]:
    value = getattr(args, argument, None)
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


_COMPILER_ENVIRONMENT_NAMES = ("CC", "CXX", "CFLAGS", "CXXFLAGS")


@contextmanager
def neutral_compiler_environment() -> Iterator[None]:
    """Unset CC/CXX/CFLAGS/CXXFLAGS for the duration.

    For anything that runs both the library build and the harness compile in one invocation:
    the build gate, and a repair agent, which reaches the gate through check_build.sh and so
    passes on whatever its own shell exports. Each generated script bakes in its own settings,
    so with nothing exported both get the right ones. Leaving one stage's environment in place
    applies its flags to both, and the harness flags in particular (`-fsanitize=fuzzer`
    supplies its own main) make cmake's compiler check and autotools' configure link test fail.
    """
    original = {name: os.environ.pop(name, None) for name in _COMPILER_ENVIRONMENT_NAMES}
    try:
        yield
    finally:
        for name, previous in original.items():
            if previous is not None:
                os.environ[name] = previous


@contextmanager
def compile_commands_capture_environment() -> Iterator[None]:
    """Ask CMake to write build/compile_commands.json for the duration.

    CMake reads this as the default for the cache entry, so the flag never has to appear in the
    generated build_library.sh -- capture stays something the harness applies, and the shipped
    script is unaffected. Meson's Ninja backend writes the file unconditionally; Make and
    Autotools have no equivalent and rely on the gate's `bear` wrap instead.
    """
    with _temporary_environment({"CMAKE_EXPORT_COMPILE_COMMANDS": "ON"}):
        yield


@contextmanager
def _temporary_environment(values: dict[str, str]) -> Iterator[None]:
    original = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, previous in original.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
