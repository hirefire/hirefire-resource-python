import time
from datetime import datetime, timezone

import pytest
from freezegun import freeze_time
from redis import Redis
from rq import Queue

from hirefire_resource.macro.rq import job_queue_latency

redis_url = "redis://localhost:6379/12"
queue_name = "default"


@pytest.fixture(autouse=True)
def clear_redis():
    r = Redis.from_url(redis_url)
    r.flushdb()


def test_latency_without_jobs():
    assert job_queue_latency("default", redis_url=redis_url) == 0


def test_latency_with_jobs():
    default = Queue("default", connection=Redis.from_url(redis_url))
    critical = Queue("critical", connection=Redis.from_url(redis_url))

    with freeze_time(datetime.fromtimestamp(time.time() - 200, timezone.utc)):
        default.enqueue("my_function")

    with freeze_time(datetime.fromtimestamp(time.time() - 100, timezone.utc)):
        critical.enqueue("my_function")

    assert job_queue_latency("default", redis_url=redis_url) == pytest.approx(
        200, abs=10
    )
    assert job_queue_latency("critical", redis_url=redis_url) == pytest.approx(
        100, abs=10
    )
    assert job_queue_latency(
        "default", "critical", redis_url=redis_url
    ) == pytest.approx(200, abs=10)


def test_latency_with_scheduled_jobs():
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

    assert job_queue_latency("default", redis_url=redis_url) == pytest.approx(
        450, abs=10
    )
