import re

from hirefire_resource.version import VERSION


def test_version():
    assert re.match(r"\d+\.\d+\.\d+", VERSION)
