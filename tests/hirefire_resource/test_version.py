import importlib
import re
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

import hirefire_resource.version
from hirefire_resource.version import VERSION


def test_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:rc\d+)?", VERSION)


def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    text = pyproject.read_text()
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match is not None
    assert VERSION == match.group(1)


def test_version_falls_back_to_unknown_when_package_metadata_is_missing():
    try:
        with patch("importlib.metadata.version", side_effect=PackageNotFoundError):
            importlib.reload(hirefire_resource.version)
            assert hirefire_resource.version.VERSION == "unknown"
    finally:
        importlib.reload(hirefire_resource.version)
