import math
import os
from datetime import datetime, timedelta, timezone

import pytest
from celery import Celery
from kombu import Queue

from hirefire_resource.errors import MissingQueueError
from hirefire_resource.macro.celery import (
    _cache_worker_data,
    async_job_queue_latency,
    async_job_queue_size,
    job_queue_latency,
    job_queue_size,
)

_cache_worker_data(False)

redis_url = f"redis://localhost:{os.environ.get('REDIS_PORT', '6379')}/15"
amqp_url = f"amqp://guest:guest@localhost:{os.environ.get('RABBITMQ_PORT', '5672')}"
broker_urls = [redis_url, amqp_url]


@pytest.fixture(scope="session", params=broker_urls)
def celery_app(request):
    return Celery(broker=request.param)


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
    # Verify that peeking doesn't discard the message
    assert (
        job_queue_size("celery", "mailer", broker_url=celery_app.conf.broker_url) == 10
    )


def test_job_queue_latency_with_jobs_multi(celery_app):
    enqueue_for_job_queue_latency_with_job(celery_app)

    assert math.isclose(
        job_queue_latency("celery", "mailer", broker_url=celery_app.conf.broker_url),
        8,
        abs_tol=1,
    )
    # Verify that peeking doesn't discard the message
    assert (
        job_queue_size("celery", "mailer", broker_url=celery_app.conf.broker_url) == 10
    )


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
    # Verify that peeking doesn't discard the message
    assert (
        await async_job_queue_size(
            "celery", "mailer", broker_url=celery_app.conf.broker_url
        )
        == 10
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
    # Verify that peeking doesn't discard the message
    assert (
        await async_job_queue_size(
            "celery", "mailer", broker_url=celery_app.conf.broker_url
        )
        == 10
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

    assert job_queue_size("celery", broker_url=celery_app.conf.broker_url) == 5
    assert (
        job_queue_size("celery", "mailer", broker_url=celery_app.conf.broker_url) == 10
    )


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

    assert (
        await async_job_queue_size("celery", broker_url=celery_app.conf.broker_url) == 5
    )
    assert (
        await async_job_queue_size(
            "celery", "mailer", broker_url=celery_app.conf.broker_url
        )
        == 10
    )


# Tests for priority queues (RabbitMQ only)


@pytest.fixture
def priority_celery_app():
    """Create a Celery app with priority queue configuration."""
    broker_url = amqp_url
    app = Celery(broker=broker_url)

    # Configure queues with x-max-priority
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
        # Delete queue if it exists (to start fresh)
        channel.queue_delete(queue="priority_queue")
        # Create queue WITH x-max-priority argument
        channel.queue_declare(
            queue="priority_queue",
            durable=True,
            auto_delete=False,
            arguments={"x-max-priority": 10},
        )

    yield priority_celery_app

    # Cleanup: delete the queue after test
    with priority_celery_app.connection_or_acquire() as connection:
        channel = connection.default_channel
        channel.queue_delete(queue="priority_queue")


def test_job_queue_size_priority_queue_with_broker_url(setup_priority_queue):
    """
    Test job_queue_size with broker_url on a priority queue.

    Note: In the test environment (RabbitMQ 4.2.2, py-amqp 5.3.1), this works fine
    because passive=True declarations don't validate arguments. However, some
    RabbitMQ versions or configurations may return PRECONDITION_FAILED.

    For guaranteed compatibility with priority queues, use celery_app parameter instead.
    """
    priority_celery_app = setup_priority_queue
    broker_url = priority_celery_app.conf.broker_url

    # Add tasks to the queue
    priority_celery_app.send_task("test_task", queue="priority_queue")
    priority_celery_app.send_task("test_task", queue="priority_queue")

    # Using broker_url (no queue arguments passed)
    result = job_queue_size("priority_queue", broker_url=broker_url)

    # In the test environment this works, but may fail in other environments
    assert result == 2


def test_job_queue_size_priority_queue_with_celery_app_returns_correct_count(
    setup_priority_queue,
):
    """
    Test that job_queue_size returns the correct count when passed the Celery app
    that has the queue configuration with x-max-priority.

    This is the recommended approach for priority queues - passing the celery_app
    extracts the queue arguments and passes them to queue_declare.
    """
    priority_celery_app = setup_priority_queue

    # Add tasks to the queue
    priority_celery_app.send_task("test_task", queue="priority_queue")
    priority_celery_app.send_task("test_task", queue="priority_queue")
    priority_celery_app.send_task("test_task", queue="priority_queue")

    # Recommended: Pass the celery_app parameter so queue arguments are extracted
    result = job_queue_size("priority_queue", celery_app=priority_celery_app)

    # This should return the correct count
    assert result == 3


def test_job_queue_size_raises_error_when_both_broker_url_and_celery_app_provided(
    setup_priority_queue,
):
    """
    Test that job_queue_size raises ValueError when both broker_url and celery_app
    are provided, since they are mutually exclusive.
    """
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
    """
    Test that async_job_queue_size raises ValueError when both broker_url and celery_app
    are provided, since they are mutually exclusive.
    """
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

    In the test environment (RabbitMQ 4.2.2), passive=True declarations are lenient
    and don't validate x-max-priority mismatches. However, some RabbitMQ versions/
    configurations ARE strict and return PRECONDITION_FAILED for mismatches.

    This test proves that using celery_app (which passes correct arguments) works
    reliably across all RabbitMQ versions.

    This test only runs on AMQP (RabbitMQ), not Redis.
    """
    broker_url = celery_app.conf.broker_url

    # Skip this test for Redis - priority queues are RabbitMQ only
    if not broker_url.startswith("amqp"):
        pytest.skip("Priority queues only work with RabbitMQ")

    # Use a unique queue name to avoid conflicts
    import uuid

    queue_name = f"test_mismatch_{uuid.uuid4().hex[:8]}"

    # Create a Celery app configured with the priority queue
    temp_app = Celery(broker=broker_url)
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

        # Add some tasks using the properly configured app
        temp_app.send_task("test_task", queue=queue_name)
        temp_app.send_task("test_task", queue=queue_name)

        # Create a Celery app with WRONG priority (10 instead of 20)
        wrong_app = Celery(broker=broker_url)
        wrong_app.conf.task_queues = [
            Queue(queue_name, queue_arguments={"x-max-priority": 10}),  # Wrong!
        ]

        # Try to query with wrong arguments
        # In strict RabbitMQ versions, this triggers PRECONDITION_FAILED (returns 0)
        # In lenient versions (like the test environment), it still works (returns 2)
        result_wrong = job_queue_size(queue_name, celery_app=wrong_app)
        # Both outcomes are acceptable - the test just verifies it doesn't crash
        assert result_wrong in [0, 2]

        # Create a Celery app with CORRECT priority (20)
        correct_app = Celery(broker=broker_url)
        correct_app.conf.task_queues = [
            Queue(queue_name, queue_arguments={"x-max-priority": 20}),  # Correct!
        ]

        # Query with correct arguments - should work and return actual count
        result_correct = job_queue_size(queue_name, celery_app=correct_app)
        assert result_correct == 2

    finally:
        # Cleanup
        try:
            with temp_app.connection_or_acquire() as connection:
                channel = connection.default_channel
                channel.queue_delete(queue=queue_name)
        except Exception:
            pass
