import importlib
import re
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

import hirefire_resource.version
from hirefire_resource.version import VERSION


def test_version():
    assert re.match(r"\d+\.\d+\.\d+", VERSION)


def test_version_falls_back_to_unknown_when_package_metadata_is_missing():
    try:
        with patch("importlib.metadata.metadata", side_effect=PackageNotFoundError):
            importlib.reload(hirefire_resource.version)
            assert hirefire_resource.version.VERSION == "unknown"
    finally:
        importlib.reload(hirefire_resource.version)
