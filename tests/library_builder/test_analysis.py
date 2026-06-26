import importlib


def test_analysis_importable() -> None:
    importlib.import_module("harnessbuddy.library_builder.analysis")
