import importlib


def test_repos_importable() -> None:
    importlib.import_module("harnessbuddy.core.repos")
