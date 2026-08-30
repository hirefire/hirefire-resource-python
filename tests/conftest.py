import logging
import os
import socket
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
    # Stop without the final flush: mocket is already torn down at this point,
    # so a flushing stop would POST to the real data.hirefire.io.
    HireFire.configuration.stop_dispatcher(flush=False)
    HireFire.reset()


@pytest.fixture(autouse=True)
def block_real_network(request, monkeypatch):
    # Path-based: without __init__.py, pytest names modules by basename, so
    # request.module.__name__ never contains "macro".
    if "macro" in request.node.path.parts:
        yield
        return

    # Mocket swaps socket.socket while active, so registered stubs still win.
    # This only fails real connections that escape mocking (e.g. dispatcher
    # loop threads firing between mocket teardown and dispatcher stop).
    def blocked(self, address):
        raise OSError("Real network access is disabled in tests.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    yield


@pytest.fixture(autouse=True)
def capture_hirefire_logs(request, caplog):
    if "macro" in request.module.__name__.split("."):
        yield
        return

    logger = logging.getLogger("hirefire_resource")
    logger.addHandler(caplog.handler)
    yield
    logger.removeHandler(caplog.handler)
