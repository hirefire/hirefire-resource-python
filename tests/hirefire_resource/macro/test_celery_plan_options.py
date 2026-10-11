import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

celery = pytest.importorskip("celery")

import redis  # noqa: E402
from celery import Celery  # noqa: E402

from hirefire_resource.macro import celery as celery_macro  # noqa: E402
from hirefire_resource.macro.celery import run_at_header_signal  # noqa: E402

redis_url = f"redis://127.0.0.1:{os.environ.get('REDIS_PORT', '6379')}/0"


@pytest.fixture
def prefix():
    prefix = f"hirefire-{uuid.uuid4().hex}:"
    yield prefix
    client = redis.Redis.from_url(redis_url)
    for key in client.scan_iter(f"{prefix}*"):
        client.delete(key)


def test_plan_connection_options_empty_without_env():
    assert celery_macro.plan_connection_options() == {}


def test_run_at_header_signal_preserves_eta():
    headers = {"eta": "2026-08-26T11:00:00+00:00", "task": "test_task"}

    run_at_header_signal(headers=headers)

    assert headers["run_at"] == headers["eta"]
    assert headers["task"] == "test_task"


def test_run_at_header_signal_sets_current_time_without_eta():
    headers = {"task": "test_task"}

    run_at_header_signal(headers=headers)

    parsed = datetime.fromisoformat(headers["run_at"])
    assert parsed.tzinfo is not None
    assert headers["task"] == "test_task"


def test_plan_connection_options_prefer_hirefire_celery_broker_url(monkeypatch):
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", "redis://hf/0")
    assert celery_macro.plan_connection_options() == {"broker_url": "redis://hf/0"}


def test_plan_connection_options_ignore_blank(monkeypatch):
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", "   ")
    assert celery_macro.plan_connection_options() == {}


def test_supports_plan_strategy():
    assert celery_macro.supports_plan_strategy("jql")
    assert celery_macro.supports_plan_strategy("jqs")
    assert not celery_macro.supports_plan_strategy("cpu")


def test_plan_options_allowlist_skip_working_for_jqs():
    options = celery_macro.plan_options(
        "jqs", {"skip_working": True, "broker_url": "redis://other/0"}
    )

    assert options == {"skip_working": True}


def test_plan_options_keep_a_false_skip_working():
    assert celery_macro.plan_options("jqs", {"skip_working": False}) == {
        "skip_working": False
    }


def test_plan_options_drop_a_non_boolean_skip_working():
    assert celery_macro.plan_options("jqs", {"skip_working": "true"}) == {}
    assert celery_macro.plan_options("jqs", {"skip_working": 1}) == {}
    assert celery_macro.plan_options("jqs", {"skip_working": None}) == {}
    assert celery_macro.plan_options("jqs", None) == {}


def test_plan_options_jql_never_receives_skip_working():
    assert celery_macro.plan_options("jql", {"skip_working": True}) == {}


def test_sample_wave_hooks_default_to_noops():
    from hirefire_resource.plan import hooks

    assert celery_macro.before_sample_job_queues is hooks.before_sample_job_queues
    assert celery_macro.after_sample_job_queues is hooks.after_sample_job_queues
    assert celery_macro.before_sample_job_queues() is None
    assert celery_macro.after_sample_job_queues("token") is None


def test_reinit_after_fork_is_the_macros_own_hook():
    from hirefire_resource.plan import hooks

    assert celery_macro.reinit_after_fork is not hooks.reinit_after_fork
    assert celery_macro.reinit_after_fork() is None


def test_the_macro_takes_the_strategies_and_options_the_server_sends():
    assert celery_macro.supports_plan_strategy("jqs") is True
    assert celery_macro.supports_plan_strategy("jql") is True
    assert celery_macro.queues_required() is True
    assert hasattr(celery_macro, "job_queue_working") is True
    assert celery_macro.plan_options("jqs", {"skip_working": True}) == {
        "skip_working": True
    }
    assert celery_macro.plan_options("jqs", {}) == {}
    assert celery_macro.plan_options("jql", {"skip_working": True}) == {}


def test_plan_source_is_the_url_a_plan_sample_reads_without_its_credentials(
    monkeypatch,
):
    monkeypatch.setenv("CELERY_BROKER_URL", "amqp://user:secret@env-host:5672/vhost")

    assert celery_macro.plan_source() == "amqp://***@env-host:5672/vhost"
    assert celery_macro.plan_source(broker_url="redis://hf/1") == "redis://hf/1"


def test_the_key_of_the_oldest_task_has_the_prefix_that_the_client_does_not_add():
    def channel(*prefixed, **attributes):
        client = SimpleNamespace(PREFIXED_SIMPLE_COMMANDS=list(prefixed))
        return SimpleNamespace(client=client, **attributes)

    with_prefix = {"global_keyprefix": "app:"}
    assert celery_macro._lindex_key(channel("LLEN", **with_prefix), "q") == "app:q"
    assert celery_macro._lindex_key(channel("LINDEX", **with_prefix), "q") == "q"
    assert celery_macro._lindex_key(channel("LLEN"), "q") == "q"
    assert celery_macro._lindex_key(SimpleNamespace(client=object()), "q") == "q"


def test_a_celery_app_with_a_key_prefix_is_read_under_its_prefix(prefix):
    queue = f"queue-{uuid.uuid4().hex}"
    app = Celery("prefixed", broker=redis_url, set_as_current=False)
    app.conf.broker_pool_limit = None
    app.conf.broker_transport_options = {"global_keyprefix": prefix}
    eta = datetime.now(timezone.utc) - timedelta(seconds=30)
    app.send_task("test_task", queue=queue, eta=eta)

    assert celery_macro.job_queue_size(queue, celery_app=app, skip_working=True) == 1
    assert 29 <= celery_macro.job_queue_latency(queue, celery_app=app) <= 60
    assert (
        celery_macro.job_queue_size(queue, broker_url=redis_url, skip_working=True) == 0
    )
    assert celery_macro.job_queue_latency(queue, broker_url=redis_url) == 0
