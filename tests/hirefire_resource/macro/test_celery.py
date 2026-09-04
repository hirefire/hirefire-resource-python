import asyncio
import math
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from celery import Celery
from kombu import Queue

from hirefire_resource import HireFire, plan
from hirefire_resource.errors import MissingQueueError
from hirefire_resource.macro.celery import (
    ChannelError,
    _job_queue_latency_rabbitmq,
    _job_queue_latency_redis,
    _resolve_broker_url,
    async_job_queue_latency,
    async_job_queue_size,
    job_queue_latency,
    job_queue_size,
)

redis_url = f"redis://localhost:{os.environ.get('REDIS_PORT', '6379')}/0"
amqp_url = f"amqp://guest:guest@localhost:{os.environ.get('RABBITMQ_PORT', '5672')}"
broker_urls = [redis_url, amqp_url]

_SIZE_WAIT_S = 2.0
_SIZE_POLL_S = 0.02


def _celery(broker_url):
    app = Celery(broker=broker_url)
    app.conf.broker_pool_limit = None
    if str(broker_url).startswith("amqp"):
        app.conf.broker_transport_options = {"confirm_publish": True}
    return app


def _assert_int_count(value):
    assert isinstance(value, int) and not isinstance(value, bool), type(value)


def _assert_float_seconds(value):
    assert isinstance(value, float), type(value)


def _assert_size(expected, *queues, **kwargs):
    deadline = time.monotonic() + _SIZE_WAIT_S
    seen = None
    while time.monotonic() < deadline:
        seen = job_queue_size(*queues, **kwargs)
        _assert_int_count(seen)
        if seen == expected:
            return
        time.sleep(_SIZE_POLL_S)
    assert seen == expected


async def _assert_async_size(expected, *queues, **kwargs):
    deadline = time.monotonic() + _SIZE_WAIT_S
    seen = None
    while time.monotonic() < deadline:
        seen = await async_job_queue_size(*queues, **kwargs)
        _assert_int_count(seen)
        if seen == expected:
            return
        await asyncio.sleep(_SIZE_POLL_S)
    assert seen == expected


@pytest.fixture(scope="session", params=broker_urls)
def celery_app(request):
    return _celery(request.param)


@pytest.fixture(autouse=True)
def clear_broker(celery_app):
    broker_url = celery_app.conf.broker_url

    if broker_url.startswith("redis://"):
        with celery_app.connection_or_acquire() as connection:
            connection.default_channel.client.flushdb()

    elif broker_url.startswith("amqp://"):
        with celery_app.connection_or_acquire() as connection:
            channel = connection.default_channel
            for queue in ["celery", "mailer"]:
                channel.queue_delete(queue=queue)
                channel.queue_declare(queue=queue, durable=True, auto_delete=False)


def test_library_loaded_is_true_when_celery_package_is_imported():
    assert plan.library_loaded("celery")
    assert plan.executable("celery")
    assert plan.any_allowlisted_job_queue_library_loaded()


def test_plan_does_not_import_celery_when_host_is_unloaded():
    import sys

    removed = {}
    keys = [
        k
        for k in list(sys.modules)
        if k == "celery"
        or k.startswith("celery.")
        or k == "hirefire_resource.macro.celery"
    ]
    for key in keys:
        removed[key] = sys.modules.pop(key)
    try:
        assert plan._load_macro("celery") is None
        with plan.around_job_queue_sample():
            pass
        plan.reinit_macros_after_fork()
        assert "celery" not in sys.modules
        assert "hirefire_resource.macro.celery" not in sys.modules
    finally:
        sys.modules.update(removed)


def test_job_queue_latency_missing_queue():
    with pytest.raises(MissingQueueError):
        job_queue_latency()


def test_job_queue_latency_without_jobs(celery_app):
    latency = job_queue_latency("celery", broker_url=celery_app.conf.broker_url)
    _assert_float_seconds(latency)
    assert latency == 0


def enqueue_for_job_queue_latency_with_job(celery_app):
    now = datetime.now(timezone.utc)

    for i in reversed(range(5)):
        celery_app.send_task(
            "test_task", queue="celery", eta=(now - timedelta(seconds=i))
        )
        celery_app.send_task(
            "test_task", queue="mailer", eta=(now - timedelta(seconds=i * 2))
        )


def test_job_queue_latency_with_jobs(celery_app):
    enqueue_for_job_queue_latency_with_job(celery_app)

    celery_latency = job_queue_latency("celery", broker_url=celery_app.conf.broker_url)
    _assert_float_seconds(celery_latency)
    assert math.isclose(
        celery_latency,
        4,
        abs_tol=1,
    )
    assert math.isclose(
        job_queue_latency("mailer", broker_url=celery_app.conf.broker_url), 8, abs_tol=1
    )
    _assert_size(10, "celery", "mailer", broker_url=celery_app.conf.broker_url)


def test_job_queue_latency_with_jobs_multi(celery_app):
    enqueue_for_job_queue_latency_with_job(celery_app)

    assert math.isclose(
        job_queue_latency("celery", "mailer", broker_url=celery_app.conf.broker_url),
        8,
        abs_tol=1,
    )
    _assert_size(10, "celery", "mailer", broker_url=celery_app.conf.broker_url)


@pytest.mark.asyncio
async def test_async_job_queue_latency_missing_queue():
    with pytest.raises(MissingQueueError):
        await async_job_queue_latency()


@pytest.mark.asyncio
async def test_job_queue_latency_without_jobs_async(celery_app):
    latency = await async_job_queue_latency(
        "celery", broker_url=celery_app.conf.broker_url
    )
    _assert_float_seconds(latency)
    assert latency == 0


@pytest.mark.asyncio
async def test_job_queue_latency_with_jobs_async(celery_app):
    enqueue_for_job_queue_latency_with_job(celery_app)

    celery_latency = await async_job_queue_latency(
        "celery", broker_url=celery_app.conf.broker_url
    )
    _assert_float_seconds(celery_latency)
    assert math.isclose(
        celery_latency,
        4,
        abs_tol=1,
    )
    assert math.isclose(
        await async_job_queue_latency("mailer", broker_url=celery_app.conf.broker_url),
        8,
        abs_tol=1,
    )
    await _assert_async_size(
        10, "celery", "mailer", broker_url=celery_app.conf.broker_url
    )


@pytest.mark.asyncio
async def test_job_queue_latency_with_jobs_multi_async(celery_app):
    enqueue_for_job_queue_latency_with_job(celery_app)

    assert math.isclose(
        await async_job_queue_latency(
            "celery", "mailer", broker_url=celery_app.conf.broker_url
        ),
        8,
        abs_tol=1,
    )
    await _assert_async_size(
        10, "celery", "mailer", broker_url=celery_app.conf.broker_url
    )


def test_job_queue_size_missing_queue():
    with pytest.raises(MissingQueueError):
        job_queue_size()


def test_job_queue_size_without_jobs(celery_app):
    size = job_queue_size("celery", broker_url=celery_app.conf.broker_url)
    _assert_int_count(size)
    assert size == 0


def test_job_queue_size_with_jobs(celery_app):
    for _ in range(5):
        celery_app.send_task("test_task", queue="celery")
        celery_app.send_task("test_task", queue="mailer")

    _assert_size(5, "celery", broker_url=celery_app.conf.broker_url)
    _assert_size(10, "celery", "mailer", broker_url=celery_app.conf.broker_url)


def test_job_queue_size_dedupes_and_trims_queue_names(celery_app):
    for _ in range(3):
        celery_app.send_task("test_task", queue="celery")

    _assert_size(
        3,
        "celery",
        " celery ",
        "celery",
        broker_url=celery_app.conf.broker_url,
    )


def test_job_queue_size_blank_queue_names_raise():
    with pytest.raises(MissingQueueError):
        job_queue_size("  ", "")


def test_mitigate_connection_reset_error_decorator():
    from hirefire_resource.macro.celery import mitigate_connection_reset_error

    call_count = [0]

    @mitigate_connection_reset_error(retries=2, delay=0)
    def flaky_function():
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionResetError("Simulated failure")
        return 42

    result = flaky_function()
    assert result == 42
    assert call_count[0] == 2


def test_mitigate_connection_reset_error_retries_zero_still_calls_once():
    from hirefire_resource.macro.celery import mitigate_connection_reset_error

    call_count = [0]

    @mitigate_connection_reset_error(retries=0, delay=0)
    def once():
        call_count[0] += 1
        return 1

    assert once() == 1
    assert call_count[0] == 1


def test_plan_drops_sample_when_celery_raises_operational_error(monkeypatch, caplog):
    import logging
    from contextlib import contextmanager

    from kombu.exceptions import OperationalError

    from hirefire_resource.macro import celery as celery_macro

    caplog.set_level(logging.ERROR)

    @contextmanager
    def boom(*_args, **_kwargs):
        raise OperationalError("broker down")
        yield None

    monkeypatch.setattr(celery_macro, "_sample_connection", boom)
    with patch.object(plan, "_load_macro", return_value=celery_macro):
        plan.execute(
            {
                "name": "worker",
                "adapter": "celery",
                "strategy": "jqs",
                "queues": ["celery"],
            }
        )

    data = HireFire.configuration.buffer.flush()
    assert data.get("worker", {}).get("jqs") is None
    assert "OperationalError" in caplog.text
    assert "broker down" in caplog.text


def test_job_queue_size_reraises_operational_error(monkeypatch):
    from contextlib import contextmanager

    from kombu.exceptions import OperationalError

    @contextmanager
    def boom():
        raise OperationalError("broker down")
        yield None

    monkeypatch.setattr(
        "hirefire_resource.macro.celery._sample_connection", lambda _app: boom()
    )
    with pytest.raises(OperationalError, match="broker down"):
        job_queue_size("celery", broker_url="redis://localhost:6379/0")


def test_job_queue_latency_reraises_operational_error(monkeypatch):
    from contextlib import contextmanager

    from kombu.exceptions import OperationalError

    @contextmanager
    def boom():
        raise OperationalError("broker down")
        yield None

    monkeypatch.setattr(
        "hirefire_resource.macro.celery._sample_connection", lambda _app: boom()
    )
    with pytest.raises(OperationalError, match="broker down"):
        job_queue_latency("celery", broker_url="redis://localhost:6379/0")


def test_mitigate_connection_reset_error_preserves_name_and_retries_once(
    monkeypatch,
):
    from hirefire_resource.macro import celery as celery_macro

    assert celery_macro.job_queue_size.__name__ == "job_queue_size"
    assert celery_macro.job_queue_latency.__name__ == "job_queue_latency"

    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    calls = [0]

    @celery_macro.mitigate_connection_reset_error()
    def flaky_function():
        calls[0] += 1
        if calls[0] == 1:
            raise ConnectionResetError("reset")
        return 7

    assert flaky_function() == 7
    assert calls[0] == 2
    assert slept == []


@pytest.mark.asyncio
async def test_async_job_queue_size_missing_queue():
    with pytest.raises(MissingQueueError):
        await async_job_queue_size()


@pytest.mark.asyncio
async def test_job_queue_size_without_jobs_async(celery_app):
    size = await async_job_queue_size("celery", broker_url=celery_app.conf.broker_url)
    _assert_int_count(size)
    assert size == 0


@pytest.mark.asyncio
async def test_job_queue_size_with_jobs_async(celery_app):
    for _ in range(5):
        celery_app.send_task("test_task", queue="celery")
        celery_app.send_task("test_task", queue="mailer")

    await _assert_async_size(5, "celery", broker_url=celery_app.conf.broker_url)
    await _assert_async_size(
        10, "celery", "mailer", broker_url=celery_app.conf.broker_url
    )


@pytest.fixture
def priority_celery_app():
    app = _celery(amqp_url)

    queue_arguments = {"x-max-priority": 10}
    app.conf.task_queues = [
        Queue("priority_queue", queue_arguments=queue_arguments),
    ]
    app.conf.task_queue_max_priority = 10
    app.conf.task_default_priority = 5

    return app


@pytest.fixture
def setup_priority_queue(priority_celery_app):
    with priority_celery_app.connection_or_acquire() as connection:
        channel = connection.default_channel
        channel.queue_delete(queue="priority_queue")
        channel.queue_declare(
            queue="priority_queue",
            durable=True,
            auto_delete=False,
            arguments={"x-max-priority": 10},
        )

    yield priority_celery_app

    with priority_celery_app.connection_or_acquire() as connection:
        channel = connection.default_channel
        channel.queue_delete(queue="priority_queue")


def test_job_queue_size_priority_queue_with_broker_url(setup_priority_queue):
    priority_celery_app = setup_priority_queue
    broker_url = priority_celery_app.conf.broker_url

    priority_celery_app.send_task("test_task", queue="priority_queue")
    priority_celery_app.send_task("test_task", queue="priority_queue")

    _assert_size(2, "priority_queue", broker_url=broker_url)


def test_job_queue_size_priority_queue_with_celery_app_returns_correct_count(
    setup_priority_queue,
):
    priority_celery_app = setup_priority_queue

    priority_celery_app.send_task("test_task", queue="priority_queue")
    priority_celery_app.send_task("test_task", queue="priority_queue")
    priority_celery_app.send_task("test_task", queue="priority_queue")

    _assert_size(3, "priority_queue", celery_app=priority_celery_app)


def test_job_queue_size_raises_error_when_both_broker_url_and_celery_app_provided(
    setup_priority_queue,
):
    priority_celery_app = setup_priority_queue

    with pytest.raises(ValueError) as exc_info:
        job_queue_size(
            "priority_queue",
            broker_url=amqp_url,
            celery_app=priority_celery_app,
        )

    assert "Cannot specify both" in str(exc_info.value)


@pytest.mark.asyncio
async def test_async_job_queue_size_raises_error_when_both_broker_url_and_celery_app_provided(
    setup_priority_queue,
):
    priority_celery_app = setup_priority_queue

    with pytest.raises(ValueError) as exc_info:
        await async_job_queue_size(
            "priority_queue",
            broker_url=amqp_url,
            celery_app=priority_celery_app,
        )

    assert "Cannot specify both" in str(exc_info.value)


def test_job_queue_size_with_mismatched_priority_arguments(celery_app):
    broker_url = celery_app.conf.broker_url

    if not broker_url.startswith("amqp"):
        pytest.skip("Priority queues only work with RabbitMQ")

    import uuid

    queue_name = f"test_mismatch_{uuid.uuid4().hex[:8]}"

    temp_app = _celery(broker_url)
    temp_app.conf.task_queues = [
        Queue(queue_name, queue_arguments={"x-max-priority": 20}),
    ]

    try:
        with temp_app.connection_or_acquire() as connection:
            channel = connection.default_channel
            channel.queue_declare(
                queue=queue_name,
                durable=True,
                auto_delete=False,
                arguments={"x-max-priority": 20},
            )

        temp_app.send_task("test_task", queue=queue_name)
        temp_app.send_task("test_task", queue=queue_name)

        wrong_app = Celery(broker=broker_url)
        wrong_app.conf.task_queues = [
            Queue(queue_name, queue_arguments={"x-max-priority": 10}),
        ]

        result_wrong = job_queue_size(queue_name, celery_app=wrong_app)
        assert result_wrong in [0, 2]

        correct_app = Celery(broker=broker_url)
        correct_app.conf.task_queues = [
            Queue(queue_name, queue_arguments={"x-max-priority": 20}),
        ]

        _assert_size(2, queue_name, celery_app=correct_app)

    finally:
        try:
            with temp_app.connection_or_acquire() as connection:
                channel = connection.default_channel
                channel.queue_delete(queue=queue_name)
        except Exception:
            pass


def test_job_queue_latency_redis_accepts_str_payload():
    run_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    payload = (
        '{"headers": {"run_at": "'
        + run_at
        + '"}, "body": "", "content-type": "application/json"}'
    )

    class FakeClient:
        def lindex(self, queue, index):
            return payload

    class FakeChannel:
        client = FakeClient()

    latency = _job_queue_latency_redis(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert math.isclose(latency, 5, abs_tol=1)


def test_job_queue_latency_redis_skips_corrupt_payload():
    class FakeClient:
        def lindex(self, queue, index):
            return "not-json"

    class FakeChannel:
        client = FakeClient()

    latency = _job_queue_latency_redis(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def test_job_queue_latency_redis_corrupt_queue_does_not_hide_sibling():
    run_at = (datetime.now(timezone.utc) - timedelta(seconds=8)).isoformat()
    payload = '{"headers": {"run_at": "' + run_at + '"}}'

    class FakeClient:
        def lindex(self, queue, index):
            return "not-json" if queue == "bad" else payload

    class FakeChannel:
        client = FakeClient()

    channel = FakeChannel()
    bad = _job_queue_latency_redis(channel, "bad")
    good = _job_queue_latency_redis(channel, "good")
    _assert_float_seconds(bad)
    _assert_float_seconds(good)
    assert bad == 0
    assert math.isclose(good, 8, abs_tol=1)


def test_job_queue_latency_rabbitmq_requeues_after_parse_error():
    class BrokenMessage:
        headers = object()
        delivery_tag = 7

    class FakeChannel:
        def __init__(self):
            self.rejected = []

        def basic_get(self, queue):
            return BrokenMessage()

        def basic_reject(self, delivery_tag, requeue=False):
            self.rejected.append((delivery_tag, requeue))

    channel = FakeChannel()
    latency = _job_queue_latency_rabbitmq(channel, "celery")
    _assert_float_seconds(latency)
    assert latency == 0

    assert channel.rejected == [(7, True)]


def test_job_queue_latency_redis_empty_queue_is_float_zero():
    class FakeClient:
        def lindex(self, queue, index):
            return None

    class FakeChannel:
        client = FakeClient()

    latency = _job_queue_latency_redis(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def test_job_queue_latency_redis_future_run_at_is_float_zero():
    run_at = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    payload = '{"headers": {"run_at": "' + run_at + '"}}'

    class FakeClient:
        def lindex(self, queue, index):
            return payload

    class FakeChannel:
        client = FakeClient()

    latency = _job_queue_latency_redis(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def test_job_queue_latency_rabbitmq_empty_queue_is_float_zero():
    class FakeChannel:
        def basic_get(self, queue):
            return None

    latency = _job_queue_latency_rabbitmq(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def test_job_queue_latency_rabbitmq_channel_error_is_float_zero():
    try:
        error = ChannelError("NOT_FOUND")
    except TypeError:
        error = ChannelError(404, "NOT_FOUND", (60, 20))

    class FakeChannel:
        def basic_get(self, queue):
            raise error

    latency = _job_queue_latency_rabbitmq(FakeChannel(), "celery")
    _assert_float_seconds(latency)
    assert latency == 0


def _inflating_inspect_control():
    due_eta = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    active_task = {
        "delivery_info": {"routing_key": "celery"},
        "id": "active-1",
    }
    reserved_task = {
        "delivery_info": {"routing_key": "celery"},
        "id": "reserved-1",
    }
    scheduled_task = {
        "eta": due_eta,
        "request": {
            "delivery_info": {"routing_key": "celery"},
            "id": "scheduled-1",
        },
    }

    class InflatingInspect:
        def active(self):
            return {"worker@host": [active_task, dict(active_task, id="active-2")]}

        def reserved(self):
            return {"worker@host": [reserved_task]}

        def scheduled(self):
            return {"worker@host": [scheduled_task]}

    class TrackingControl:
        def __init__(self):
            self.inspect_calls = 0

        def inspect(self, *args, **kwargs):
            self.inspect_calls += 1
            return InflatingInspect()

    return TrackingControl()


def test_job_queue_size_ignores_active_reserved_and_due_scheduled_inspect(
    celery_app, monkeypatch
):
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="celery")

    control = _inflating_inspect_control()
    monkeypatch.setattr(celery_app, "control", control)

    _assert_size(2, "celery", celery_app=celery_app)
    assert control.inspect_calls == 0


def test_job_queue_size_without_broker_jobs_ignores_worker_inspect(
    celery_app, monkeypatch
):
    control = _inflating_inspect_control()
    monkeypatch.setattr(celery_app, "control", control)

    assert job_queue_size("celery", celery_app=celery_app) == 0
    assert control.inspect_calls == 0


def test_job_queue_size_mixed_broker_and_inspect_counts_broker_only(
    celery_app, monkeypatch
):
    celery_app.send_task("test_task", queue="celery")
    celery_app.send_task("test_task", queue="mailer")
    celery_app.send_task("test_task", queue="mailer")

    control = _inflating_inspect_control()
    monkeypatch.setattr(celery_app, "control", control)

    _assert_size(3, "celery", "mailer", celery_app=celery_app)
    assert control.inspect_calls == 0


def _clear_broker_env(monkeypatch):
    for key in (
        "HIREFIRE_CELERY_BROKER_URL",
        "AMQP_URL",
        "RABBITMQ_URL",
        "RABBITMQ_BIGWIG_URL",
        "CLOUDAMQP_URL",
        "REDIS_TLS_URL",
        "REDIS_URL",
        "REDISTOGO_URL",
        "REDISCLOUD_URL",
        "OPENREDIS_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolve_broker_url_multi_key_precedence(monkeypatch):
    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("OPENREDIS_URL", "redis://openredis/0")
    monkeypatch.setenv("REDISCLOUD_URL", "redis://rediscloud/0")
    monkeypatch.setenv("REDISTOGO_URL", "redis://redistogo/0")
    monkeypatch.setenv("REDIS_URL", "redis://redis-url/0")
    monkeypatch.setenv("REDIS_TLS_URL", "rediss://redis-tls/0")
    monkeypatch.setenv("CLOUDAMQP_URL", "amqp://cloudamqp")
    monkeypatch.setenv("RABBITMQ_BIGWIG_URL", "amqp://bigwig")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://rabbitmq-url")
    monkeypatch.setenv("AMQP_URL", "amqp://amqp-url")
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", "redis://hirefire/0")
    assert _resolve_broker_url(None) == "amqp://amqp-url"

    monkeypatch.delenv("AMQP_URL")
    assert _resolve_broker_url(None) == "amqp://rabbitmq-url"


def test_resolve_broker_url_explicit_wins_over_env(monkeypatch):
    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://from-env/0")
    assert _resolve_broker_url("redis://explicit/0") == "redis://explicit/0"


def test_hirefire_celery_broker_url_is_plan_only(monkeypatch):
    import hirefire_resource.macro.celery as celery_macro

    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("HIREFIRE_CELERY_BROKER_URL", "redis://hf/0")
    monkeypatch.setenv("REDIS_URL", "redis://local/0")
    assert _resolve_broker_url(None) == "redis://local/0"
    assert celery_macro.plan_connection_options() == {"broker_url": "redis://hf/0"}
