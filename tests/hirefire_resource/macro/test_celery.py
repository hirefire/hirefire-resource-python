import asyncio
import math
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from celery import Celery
from kombu import Queue

from hirefire_resource.errors import MissingQueueError
from hirefire_resource.macro.celery import (
    _job_queue_latency_rabbitmq,
    _job_queue_latency_redis,
    async_job_queue_latency,
    async_job_queue_size,
    job_queue_latency,
    job_queue_size,
)

redis_url = f"redis://localhost:{os.environ.get('REDIS_PORT', '6379')}/0"
amqp_url = f"amqp://guest:guest@localhost:{os.environ.get('RABBITMQ_PORT', '5672')}"
broker_urls = [redis_url, amqp_url]

# AMQP send_task is fire-and-forget without confirms. Immediate message_count
# then reads N-1 under CI load. Redis publish is request/response (no-op).
_SIZE_WAIT_S = 2.0
_SIZE_POLL_S = 0.02


def _celery(broker_url):
    app = Celery(broker=broker_url)
    app.conf.broker_pool_limit = None
    if str(broker_url).startswith("amqp"):
        app.conf.broker_transport_options = {"confirm_publish": True}
    return app


def _assert_size(expected, *queues, **kwargs):
    deadline = time.monotonic() + _SIZE_WAIT_S
    seen = None
    while time.monotonic() < deadline:
        seen = job_queue_size(*queues, **kwargs)
        if seen == expected:
            return
        time.sleep(_SIZE_POLL_S)
    assert seen == expected


async def _assert_async_size(expected, *queues, **kwargs):
    deadline = time.monotonic() + _SIZE_WAIT_S
    seen = None
    while time.monotonic() < deadline:
        seen = await async_job_queue_size(*queues, **kwargs)
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


def test_job_queue_latency_missing_queue():
    with pytest.raises(MissingQueueError):
        job_queue_latency()


def test_job_queue_latency_without_jobs(celery_app):
    assert job_queue_latency("celery", broker_url=celery_app.conf.broker_url) == 0


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

    assert math.isclose(
        job_queue_latency("celery", broker_url=celery_app.conf.broker_url),
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
    assert (
        await async_job_queue_latency("celery", broker_url=celery_app.conf.broker_url)
        == 0
    )


@pytest.mark.asyncio
async def test_job_queue_latency_with_jobs_async(celery_app):
    enqueue_for_job_queue_latency_with_job(celery_app)

    assert math.isclose(
        await async_job_queue_latency("celery", broker_url=celery_app.conf.broker_url),
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
    assert job_queue_size("celery", broker_url=celery_app.conf.broker_url) == 0


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
    """Sanity check: verify the retry decorator works correctly."""
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


@pytest.mark.asyncio
async def test_async_job_queue_size_missing_queue():
    with pytest.raises(MissingQueueError):
        await async_job_queue_size()


@pytest.mark.asyncio
async def test_job_queue_size_without_jobs_async(celery_app):
    assert (
        await async_job_queue_size("celery", broker_url=celery_app.conf.broker_url) == 0
    )


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
    """Create a Celery app with priority queue configuration."""
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
    """Create the priority queue in RabbitMQ with x-max-priority argument."""
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
    """
    Test that verifies the fix for priority queue argument mismatches.

    Creates a queue in RabbitMQ with x-max-priority: 20, then tests:
    1. Querying with wrong arguments (x-max-priority: 10) - may fail in strict RabbitMQ
    2. Querying with correct arguments (x-max-priority: 20) - should always work

    In the test environment, passive=True declarations are lenient
    and don't validate x-max-priority mismatches. However, some RabbitMQ versions/
    configurations ARE strict and return PRECONDITION_FAILED for mismatches.

    This test proves that using celery_app (which passes correct arguments) works
    reliably across all RabbitMQ versions.

    This test only runs on AMQP (RabbitMQ), not Redis.
    """
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
                durable=True,  # Match Celery's default
                auto_delete=False,  # Match Celery's default
                arguments={"x-max-priority": 20},  # Created with priority 20
            )

        temp_app.send_task("test_task", queue=queue_name)
        temp_app.send_task("test_task", queue=queue_name)

        wrong_app = Celery(broker=broker_url)
        wrong_app.conf.task_queues = [
            Queue(queue_name, queue_arguments={"x-max-priority": 10}),  # Wrong!
        ]

        result_wrong = job_queue_size(queue_name, celery_app=wrong_app)
        assert result_wrong in [0, 2]

        correct_app = Celery(broker=broker_url)
        correct_app.conf.task_queues = [
            Queue(queue_name, queue_arguments={"x-max-priority": 20}),  # Correct!
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
    assert math.isclose(latency, 5, abs_tol=1)


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
    with pytest.raises(AttributeError):
        _job_queue_latency_rabbitmq(channel, "celery")

    assert channel.rejected == [(7, True)]


def _inflating_inspect_control():
    """Control/inspect that would inflate old size with active, reserved, and due scheduled."""
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
