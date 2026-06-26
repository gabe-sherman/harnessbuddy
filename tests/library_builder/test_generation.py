import importlib


def test_generation_importable() -> None:
    importlib.import_module("harnessbuddy.library_builder.generation")
