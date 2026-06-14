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

_HIREFIRE_ENV = [
    "HIREFIRE_TOKEN",
    "HIREFIRE_DATA_URL",
    "HIREFIRE_VERBOSE",
    "HIREFIRE_SERVICE_NAME",
    "DYNO",
    "RENDER_SERVICE_NAME",
]


# Every test starts from a clean configuration and a clean identity/token
# environment, and any dispatcher thread is stopped afterwards. The macro tests
# don't use the HireFire singleton (they call the queue-metric functions
# directly), so they are left untouched — their own fixtures own their broker
# state, and this fixture must not perturb their environment or timing.
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
