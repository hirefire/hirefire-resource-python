import math
from datetime import datetime, timedelta, timezone

import pytest
from celery import Celery

from hirefire_resource.errors import MissingQueueError
from hirefire_resource.macro.celery import (
    _cache_worker_data,
    async_job_queue_latency,
    async_job_queue_size,
    job_queue_latency,
    job_queue_size,
)

_cache_worker_data(False)

broker_urls = ["redis://localhost:6379/15", "amqp://guest:guest@localhost:5672"]


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
