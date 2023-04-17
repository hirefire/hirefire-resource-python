import time
from datetime import datetime, timezone

import pytest
from freezegun import freeze_time
from redis import Redis
from rq import Queue

from hirefire_resource.macro.rq import (
    async_job_queue_latency,
    async_job_queue_size,
    job_queue_latency,
    job_queue_size,
)

redis_url = "redis://localhost:6379/15"
queue_name = "default"


@pytest.fixture(autouse=True)
def clear_redis():
    r = Redis.from_url(redis_url)
    r.flushdb()


def test_job_queue_latency_default_redis_url():
    assert job_queue_size("test_default_redis_url") == 0


def test_job_queue_latency_without_jobs():
    assert job_queue_latency(redis_url=redis_url) == 0


def test_job_queue_latency_with_jobs():
    default = Queue("default", connection=Redis.from_url(redis_url))
    critical = Queue("critical", connection=Redis.from_url(redis_url))

    with freeze_time(datetime.fromtimestamp(time.time() - 200, timezone.utc)):
        default.enqueue("my_function")

    with freeze_time(datetime.fromtimestamp(time.time() - 100, timezone.utc)):
        critical.enqueue("my_function")

    assert job_queue_latency(redis_url=redis_url) == pytest.approx(200, abs=10)
    assert job_queue_latency("default", redis_url=redis_url) == pytest.approx(
        200, abs=10
    )
    assert job_queue_latency("critical", redis_url=redis_url) == pytest.approx(
        100, abs=10
    )
    assert job_queue_latency(
        "default", "critical", redis_url=redis_url
    ) == pytest.approx(200, abs=10)


def test_job_queue_latency_with_scheduled_jobs():
    default = Queue("default", connection=Redis.from_url(redis_url))
    default.enqueue_at(
        datetime.fromtimestamp(time.time() + 150, timezone.utc), "my_function"
    )
    default.enqueue_at(
        datetime.fromtimestamp(time.time() - 450, timezone.utc), "my_function"
    )
    default.enqueue_at(
        datetime.fromtimestamp(time.time() - 300, timezone.utc), "my_function"
    )
    default.enqueue_at(
        datetime.fromtimestamp(time.time() - 150, timezone.utc), "my_function"
    )

    assert job_queue_latency(redis_url=redis_url) == pytest.approx(450, abs=10)
    assert job_queue_latency("default", redis_url=redis_url) == pytest.approx(
        450, abs=10
    )
    assert job_queue_latency("critical", redis_url=redis_url) == pytest.approx(
        0, abs=10
    )


@pytest.mark.asyncio
async def test_async_job_queue_latency():
    default = Queue("default", connection=Redis.from_url(redis_url))

    with freeze_time(datetime.fromtimestamp(time.time() - 200, timezone.utc)):
        default.enqueue("my_function")

    assert await async_job_queue_latency(redis_url=redis_url) == pytest.approx(
        200, abs=10
    )
    assert await async_job_queue_latency(
        "default", redis_url=redis_url
    ) == pytest.approx(200, abs=10)
    assert await async_job_queue_latency(
        "critical", redis_url=redis_url
    ) == pytest.approx(0, abs=10)


def test_job_queue_size_default_redis_url():
    assert job_queue_size("test_default_redis_url") == 0


def test_job_queue_size_without_jobs():
    assert job_queue_size(redis_url=redis_url) == 0


def test_job_queue_size_with_jobs():
    default = Queue("default", connection=Redis.from_url(redis_url))
    critical = Queue("critical", connection=Redis.from_url(redis_url))

    default.enqueue("my_function")
    critical.enqueue("my_function")

    default.enqueue_at(
        datetime.fromtimestamp(time.time() - 100, timezone.utc), "my_function"
    )
    default.enqueue_at(
        datetime.fromtimestamp(time.time() + 100, timezone.utc), "my_function"
    )

    assert job_queue_size(redis_url=redis_url) == 3
    assert job_queue_size("default", redis_url=redis_url) == 2
    assert job_queue_size("critical", redis_url=redis_url) == 1
    assert job_queue_size("default", "critical", redis_url=redis_url) == 3


@pytest.mark.asyncio
async def test_async_job_queue_size():
    default = Queue("default", connection=Redis.from_url(redis_url))
    default.enqueue("my_function")
    assert await async_job_queue_size(redis_url=redis_url) == 1
    assert await async_job_queue_size("default", redis_url=redis_url) == 1
    assert await async_job_queue_size("critical", redis_url=redis_url) == 0
