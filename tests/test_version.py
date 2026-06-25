import re

from harnessbuddy import __version__


def test_version_is_non_empty_semver() -> None:
    assert __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
