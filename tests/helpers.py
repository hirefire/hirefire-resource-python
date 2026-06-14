import datetime

import pytest

HIREFIRE_TOKEN = "d2e39e50-82b1-478e-a457-5a53bfa153a1"


@pytest.fixture
def set_HIREFIRE_TOKEN(reset_hirefire, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", HIREFIRE_TOKEN)
    return HIREFIRE_TOKEN


# Builds a timezone-aware datetime at a Unix epoch second, for use with
# freezegun's freeze_time so time.time() returns exactly that second.
def at(epoch):
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
