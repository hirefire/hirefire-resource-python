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
    if "macro" in request.node.path.parts:
        yield
        return

    from hirefire_resource import HireFire

    for key in _HIREFIRE_ENV:
        monkeypatch.delenv(key, raising=False)
    HireFire.reset()
    yield

    HireFire.configuration.stop_dispatcher(flush=False)
    HireFire.reset()


@pytest.fixture(autouse=True)
def block_real_network(request, monkeypatch):
    if "macro" in request.node.path.parts:
        yield
        return

    allowed = set()
    connect = socket.socket.connect
    resolve = socket.getaddrinfo

    def blocked(*args, **kwargs):
        raise OSError("Real network access is disabled in tests.")

    def guarded(self, address):
        if address in allowed:
            return connect(self, address)
        return blocked()

    def guarded_resolve(host, *args, **kwargs):
        if host in ("127.0.0.1", "localhost"):
            return resolve(host, *args, **kwargs)
        return blocked()

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_resolve)
    yield allowed


@pytest.fixture
def allow_network(block_real_network):
    return block_real_network.add


@pytest.fixture
def fake_server(allow_network):
    from tests.fake_server import FakeServer

    server = FakeServer()
    allow_network(server.address)
    yield server
    server.stop()


@pytest.fixture
def load_macros(monkeypatch):
    from hirefire_resource import plan

    def load(**macros):
        monkeypatch.setattr(plan, "load_macro", macros.get)

    return load


@pytest.fixture(autouse=True)
def capture_hirefire_logs(request, caplog):
    if "macro" in request.node.path.parts:
        yield
        return

    logger = logging.getLogger("hirefire_resource")
    logger.addHandler(caplog.handler)
    yield
    logger.removeHandler(caplog.handler)
