import datetime
from types import SimpleNamespace

import pytest

from hirefire_resource.plan import hooks

HIREFIRE_TOKEN = "d2e39e50-82b1-478e-a457-5a53bfa153a1"


@pytest.fixture
def set_HIREFIRE_TOKEN(reset_hirefire, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", HIREFIRE_TOKEN)
    return HIREFIRE_TOKEN


def at(epoch):
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)


def fake_macro(**overrides):
    macro = SimpleNamespace(
        plan_options=hooks.plan_options,
        plan_connection_options=hooks.plan_connection_options,
        supports_plan_strategy=hooks.supports_plan_strategy,
        queues_required=hooks.queues_required,
        before_sample_job_queues=hooks.before_sample_job_queues,
        after_sample_job_queues=hooks.after_sample_job_queues,
        reinit_after_fork=hooks.reinit_after_fork,
        release=hooks.release,
    )
    macro.__dict__.update(overrides)
    return macro


def sample_plan(*entries, live=lambda: True):
    from hirefire_resource import HireFire
    from hirefire_resource.sampler import Sampler

    return Sampler(HireFire.configuration).round(list(entries), live)
