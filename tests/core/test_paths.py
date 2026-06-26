import importlib


def test_paths_importable() -> None:
    importlib.import_module("harnessbuddy.core.paths")
