import importlib
import pkgutil

import hirefire_resource
from tests.helpers import missing_public_names


def test_every_name_that_a_module_declares_public_exists():
    missing = []
    for info in pkgutil.walk_packages(hirefire_resource.__path__, "hirefire_resource."):
        try:
            module = importlib.import_module(info.name)
        except ImportError:
            continue
        missing += missing_public_names(module)

    assert missing_public_names(hirefire_resource) + missing == []
