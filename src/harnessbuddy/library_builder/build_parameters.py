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
        )

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

    def to_dict(self) -> dict[str, str]:
        """Return the effective settings suitable for published run metadata."""
        return {
            "cc": self.cc,
            "cxx": self.cxx,
            "library_cflags": self.library_cflags,
            "library_cxxflags": self.library_cxxflags,
            "harness_cflags": self.harness_cflags,
            "harness_cxxflags": self.harness_cxxflags,
        }


def _argument_or_environment(args: object, argument: str, environment: str, default: str) -> str:
    value = getattr(args, argument, None)
    return value if isinstance(value, str) else os.environ.get(environment, default)


def _argument_or_default(args: object, argument: str, default: str) -> str:
    value = getattr(args, argument, None)
    return value if isinstance(value, str) else default


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
