import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

celery = pytest.importorskip("celery")

import redis  # noqa: E402
from celery import Celery, _state  # noqa: E402

from hirefire_resource.macro import celery as celery_macro  # noqa: E402
from hirefire_resource.macro.celery import run_at_header_signal  # noqa: E402

redis_url = f"redis://127.0.0.1:{os.environ.get('REDIS_PORT', '6379')}/0"


def _app(name="host"):
    return Celery(name, set_as_current=False)


def _process_has(monkeypatch, *apps, default=None):
    monkeypatch.setattr(_state, "_get_active_apps", lambda: apps)
    monkeypatch.setattr(_state, "default_app", default)


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


def test_a_plan_sample_follows_the_one_celery_app_of_the_process(monkeypatch):
    app = _app()
    _process_has(monkeypatch, app)

    assert celery_macro.plan_connection_options() == {"celery_app": app}


def test_a_plan_sample_reads_by_url_when_the_process_has_several_celery_apps(
    monkeypatch,
):
    _process_has(monkeypatch, _app("first"), _app("second"))

    assert celery_macro.plan_connection_options() == {}


def test_an_app_the_macro_built_is_not_an_app_of_the_process(monkeypatch):
    app = _app()
    owned = celery_macro._owned_celery_app("redis://localhost:6379/0")

    _process_has(monkeypatch, owned)
    assert celery_macro.plan_connection_options() == {}

    _process_has(monkeypatch, owned, app)
    assert celery_macro.plan_connection_options() == {"celery_app": app}


def test_the_fallback_app_of_celery_is_not_an_app_of_the_process(monkeypatch):
    app = _app()
    fallback = Celery("default", fixups=[], set_as_current=False)

    _process_has(monkeypatch, fallback, default=fallback)
    assert celery_macro.plan_connection_options() == {}

    _process_has(monkeypatch, fallback, app, default=fallback)
    assert celery_macro.plan_connection_options() == {"celery_app": app}


def test_the_default_app_of_the_process_is_followed_among_several(monkeypatch):
    worker = _app("worker")
    _process_has(monkeypatch, _app("other"), worker, default=worker)

    assert celery_macro.plan_connection_options() == {"celery_app": worker}


def test_a_default_app_that_the_process_set_is_followed_whatever_it_looks_like(
    monkeypatch,
):
    named_default = _app("default")
    named_default.set_as_current = True
    _process_has(monkeypatch, _app("other"), named_default, default=named_default)
    assert celery_macro.plan_connection_options() == {"celery_app": named_default}

    not_current = _app("proj")
    _process_has(monkeypatch, _app("other"), not_current, default=not_current)
    assert celery_macro.plan_connection_options() == {"celery_app": not_current}


def test_an_app_that_looks_like_the_fallback_and_is_not_the_default_counts(
    monkeypatch,
):
    app = _app("default")
    _process_has(monkeypatch, app)

    assert celery_macro.plan_connection_options() == {"celery_app": app}


def test_hirefire_celery_broker_url_wins_over_the_app_of_the_process(monkeypatch):
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", "redis://hf/0")
    _process_has(monkeypatch, _app())

    assert celery_macro.plan_connection_options() == {"broker_url": "redis://hf/0"}


def test_a_list_of_apps_that_changes_while_it_is_read_is_read_again(monkeypatch):
    app = _app()
    reads = []

    def changing():
        reads.append(1)
        if len(reads) == 1:
            raise RuntimeError("Set changed size during iteration")
        return (app,)

    _process_has(monkeypatch)
    monkeypatch.setattr(_state, "_get_active_apps", changing)

    assert celery_macro.plan_connection_options() == {"celery_app": app}


def test_a_list_of_apps_that_cannot_be_read_leaves_the_sample_to_the_url(monkeypatch):
    def changing():
        raise RuntimeError("Set changed size during iteration")

    _process_has(monkeypatch)
    monkeypatch.setattr(_state, "_get_active_apps", changing)
    assert celery_macro.plan_connection_options() == {}

    monkeypatch.delattr(_state, "_get_active_apps")
    assert celery_macro.plan_connection_options() == {}
    assert celery_macro.plan_source(broker_url=None).startswith(("amqp://", "redis://"))


def test_plan_source_names_the_celery_app_a_plan_sample_follows():
    assert (
        celery_macro.plan_source(celery_app=_app("proj"))
        == "the Celery app 'proj' of this process"
    )
    assert (
        celery_macro.plan_source(celery_app=Celery(set_as_current=False))
        == "the Celery app of this process"
    )


def test_plan_source_says_why_no_app_is_followed_when_the_process_has_several(
    monkeypatch,
):
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://env-host/0")

    _process_has(monkeypatch, _app("first"), _app("second"))
    assert celery_macro.plan_source() == (
        "redis://env-host/0 from the environment, since this process has 2 Celery apps"
    )
    assert celery_macro.plan_source(broker_url="redis://hf/1") == "redis://hf/1"

    _process_has(monkeypatch, _app())
    assert celery_macro.plan_source() == "redis://env-host/0"


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


FOLLOW_THE_APP_OF_THE_PROCESS = """
import json
import sys
import threading
from datetime import datetime, timedelta, timezone

from celery import Celery

from hirefire_resource.macro import celery as macro

broker, prefix, queue = sys.argv[1:4]
host = Celery("host", broker=broker)
host.conf.broker_transport_options = {"global_keyprefix": prefix}
eta = datetime.now(timezone.utc) - timedelta(seconds=30)
host.send_task("test_task", queue=queue, eta=eta)
result = {}


def sample():
    options = macro.plan_connection_options()
    result["follows"] = options["celery_app"].main
    result["source"] = macro.plan_source(**options)
    result["size"] = macro.job_queue_size(queue, skip_working=True, **options)
    result["latency"] = macro.job_queue_latency(queue, **options)
    result["size_by_url"] = macro.job_queue_size(
        queue, broker_url=broker, skip_working=True
    )


thread = threading.Thread(target=sample)
thread.start()
thread.join()
second = Celery("second", broker=broker, set_as_current=False)
result["with_a_second_app"] = macro.plan_connection_options()
result["source_with_a_second_app"] = macro.plan_source()
print(json.dumps(result))
"""


def test_a_process_with_one_celery_app_is_sampled_through_that_app(prefix):
    queue = f"queue-{uuid.uuid4().hex}"

    done = subprocess.run(
        [sys.executable, "-c", FOLLOW_THE_APP_OF_THE_PROCESS, redis_url, prefix, queue],
        env={**os.environ, "CELERY_BROKER_URL": redis_url},
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    result = json.loads(done.stdout.splitlines()[-1])
    assert 29 <= result.pop("latency") <= 60
    assert result == {
        "follows": "host",
        "source": "the Celery app 'host' of this process",
        "size": 1,
        "size_by_url": 0,
        "with_a_second_app": {},
        "source_with_a_second_app": (
            f"{redis_url} from the environment, since this process has 2 Celery apps"
        ),
    }
