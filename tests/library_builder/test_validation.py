import importlib


def test_validation_importable() -> None:
    importlib.import_module("harnessbuddy.library_builder.validation")
