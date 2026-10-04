import importlib
from importlib.metadata import version

import pytest

import crowley

LAYER_PACKAGES = [
    "crowley.domain",
    "crowley.application",
    "crowley.stdlib",
    "crowley.infrastructure",
    "crowley.interface",
]


def test_version_matches_installed_metadata() -> None:
    assert crowley.__version__ == version("crowley")


@pytest.mark.parametrize("name", LAYER_PACKAGES)
def test_layer_package_imports(name: str) -> None:
    assert importlib.import_module(name).__doc__
