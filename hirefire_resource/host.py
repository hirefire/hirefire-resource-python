import importlib
from typing import Any


def _embedded(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


uwsgi = _embedded("uwsgi")


def under_uwsgi() -> bool:
    return uwsgi is not None


def uwsgi_parent() -> bool:
    return uwsgi is not None and uwsgi.worker_id() == 0
