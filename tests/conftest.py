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


# Load .env (written by bin/services up) so the macro suites reach this checkout's services.
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
    "DYNO",
    "RENDER_SERVICE_NAME",
    "RENDER",
    "RENDER_CPU_COUNT",
]


# Every test starts from a clean configuration and identity/token environment, and
# any dispatcher thread is stopped afterwards. The macro tests call the queue-metric
# functions directly (no singleton), so they are left untouched.
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


# The library's logger does not propagate (so it never double-emits into a host
# app's root logging), so caplog (which captures via the root) needs its handler
# attached directly to capture HireFire's log output.
@pytest.fixture(autouse=True)
def capture_hirefire_logs(request, caplog):
    if "macro" in request.module.__name__.split("."):
        yield
        return

    logger = logging.getLogger("hirefire_resource")
    logger.addHandler(caplog.handler)
    yield
    logger.removeHandler(caplog.handler)
