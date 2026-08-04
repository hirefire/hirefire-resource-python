import logging
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            SECRET_KEY="dummy-secret-key",
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
        )
        django.setup()
except ImportError:
    pass


def _load_worktree_env():
    path = os.path.join(os.path.dirname(__file__), os.pardir, ".env")
    if not os.path.exists(path):
        return
    with open(path) as env_file:
        for line in env_file:
            entry = line.strip()
            if not entry or entry.startswith("#") or "=" not in entry:
                continue
            key, value = entry.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_worktree_env()


_HIREFIRE_ENV = [
    "HIREFIRE_TOKEN",
    "HIREFIRE_DATA_URL",
    "HIREFIRE_VERBOSE",
    "HIREFIRE_SERVICE_NAME",
    "HIREFIRE_CELERY_BROKER_URL",
    "HIREFIRE_RQ_URL",
    "HIREFIRE_DRAMATIQ_URL",
    "HIREFIRE_DRAMATIQ_NAMESPACE",
    "DYNO",
    "RENDER_SERVICE_NAME",
    "RENDER_SERVICE_TYPE",
    "RENDER",
    "RENDER_CPU_COUNT",
]


@pytest.fixture(autouse=True)
def reset_hirefire(request, monkeypatch):
    if "macro" in request.module.__name__.split("."):
        yield
        return

    from hirefire_resource import HireFire

    for key in _HIREFIRE_ENV:
        monkeypatch.delenv(key, raising=False)
    HireFire.reset()
    yield
    HireFire.reset()


# The hirefire_resource logger does not propagate, so caplog (which captures via the
# root logger) needs its handler attached directly to see HireFire's output.
@pytest.fixture(autouse=True)
def capture_hirefire_logs(request, caplog):
    if "macro" in request.module.__name__.split("."):
        yield
        return

    logger = logging.getLogger("hirefire_resource")
    logger.addHandler(caplog.handler)
    yield
    logger.removeHandler(caplog.handler)
