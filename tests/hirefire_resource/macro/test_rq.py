import os
import time
from datetime import datetime, timezone

import pytest
from freezegun import freeze_time
from redis import Redis
from rq import Queue

from hirefire_resource.macro.rq import (
    _is_due_scheduled_score,
    async_job_queue_latency,
    async_job_queue_size,
    job_queue_latency,
    job_queue_size,
)

redis_url = f"redis://localhost:{os.environ.get('REDIS_PORT', '6379')}/0"
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


def test_job_queue_size_with_decode_responses():
    default = Queue("default", connection=Redis.from_url(redis_url))
    default.enqueue("my_function")

    decode_url = (
        redis_url + ("&" if "?" in redis_url else "?") + "decode_responses=true"
    )
    assert job_queue_size(redis_url=decode_url) == 1
    assert job_queue_size("default", redis_url=decode_url) == 1


def test_job_queue_size_with_colon_in_queue_name():
    dotted = Queue("tenant:mailer", connection=Redis.from_url(redis_url))
    dotted.enqueue("my_function")
    dotted.enqueue_at(
        datetime.fromtimestamp(time.time() - 100, timezone.utc), "my_function"
    )

    assert job_queue_size("tenant:mailer", redis_url=redis_url) == 2
    assert job_queue_size(redis_url=redis_url) == 2


def test_job_queue_size_dedupes_and_trims_queue_names():
    default = Queue("default", connection=Redis.from_url(redis_url))
    default.enqueue("my_function")
    default.enqueue("my_function")

    assert job_queue_size("default", " default ", "default", redis_url=redis_url) == 2


def test_job_queue_latency_with_colon_in_queue_name():
    dotted = Queue("tenant:mailer", connection=Redis.from_url(redis_url))

    with freeze_time(datetime.fromtimestamp(time.time() - 200, timezone.utc)):
        dotted.enqueue("my_function")

    assert job_queue_latency("tenant:mailer", redis_url=redis_url) == pytest.approx(
        200, abs=10
    )
    assert job_queue_latency(redis_url=redis_url) == pytest.approx(200, abs=10)


def test_job_queue_latency_with_decode_responses():
    default = Queue("default", connection=Redis.from_url(redis_url))

    with freeze_time(datetime.fromtimestamp(time.time() - 200, timezone.utc)):
        default.enqueue("my_function")

    decode_url = (
        redis_url + ("&" if "?" in redis_url else "?") + "decode_responses=true"
    )
    assert job_queue_latency(redis_url=decode_url) == pytest.approx(200, abs=10)
    assert job_queue_latency("default", redis_url=decode_url) == pytest.approx(
        200, abs=10
    )


def test_is_due_scheduled_score_is_inclusive():
    """Pure residual for the JQL due filter (score ≤ now). Fails under strict `<`."""
    now = 1_722_600_000.25
    assert _is_due_scheduled_score(now - 1.0, now) is True
    assert _is_due_scheduled_score(now, now) is True
    assert _is_due_scheduled_score(now + 0.001, now) is False
    # Strict `<` would treat equality as future. This is the lock for that bug.
    assert _is_due_scheduled_score(now, now) != (now < now)


def test_job_queue_size_excludes_wip_and_future_scheduled():
    """Waiting-only: live + due scheduled. WIP and future schedule are out."""
    r = Redis.from_url(redis_url)
    now = time.time()

    r.rpush("rq:queue:default", "live-job-1")
    r.sadd("rq:queues", "rq:queue:default")
    r.zadd(
        "rq:scheduled:default",
        {
            "due-job": now - 50,
            "due-at-now": now,
            "future-job": now + 120,
        },
    )
    r.zadd("rq:wip:default", {"working-job": now - 10})

    assert job_queue_size("default", redis_url=redis_url) == 3
    assert job_queue_size(redis_url=redis_url) == 3


def test_job_queue_size_includes_scheduled_score_equal_to_now():
    """JQS ZCOUNT max is inclusive (score ≤ now) at a whole second."""
    frozen = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
    with freeze_time(frozen):
        now = time.time()
        r = Redis.from_url(redis_url)
        r.zadd("rq:scheduled:default", {"edge-due": now})
        r.sadd("rq:queues", "rq:queue:default")

        assert job_queue_size("default", redis_url=redis_url) == 1
        assert job_queue_size(redis_url=redis_url) == 1


def test_job_queue_size_includes_fractional_second_due_score():
    """JQS uses float now for ZCOUNT max, not int(time.time()) truncation."""
    # 12:00:00.750 so int(now) == floor second. Score in (int(now), now] is due.
    frozen = datetime(2026, 8, 2, 12, 0, 0, 750_000, tzinfo=timezone.utc)
    with freeze_time(frozen):
        now = time.time()
        assert now != int(now)
        fractional_due = int(now) + 0.5
        assert int(now) < fractional_due <= now

        r = Redis.from_url(redis_url)
        r.zadd("rq:scheduled:default", {"fractional-due": fractional_due})
        r.sadd("rq:queues", "rq:queue:default")

        assert job_queue_size("default", redis_url=redis_url) == 1
        assert job_queue_size(redis_url=redis_url) == 1


def test_job_queue_latency_scheduled_due_age_and_empty_edge():
    """Scheduled latency is age of earliest due. Exact-now due contributes 0 age."""
    frozen = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
    with freeze_time(frozen):
        now = time.time()
        r = Redis.from_url(redis_url)
        r.zadd("rq:scheduled:default", {"edge-due": now})
        r.sadd("rq:queues", "rq:queue:default")

        # Age 0 at the sample clock (does not alone prove inclusive filter:
        # that residual is test_is_due_scheduled_score_is_inclusive).
        assert job_queue_latency("default", redis_url=redis_url) == 0

        r.zadd("rq:scheduled:default", {"late-due": now - 90})
        assert job_queue_latency("default", redis_url=redis_url) == pytest.approx(
            90, abs=0.01
        )
        assert job_queue_size("default", redis_url=redis_url) == 2


def test_job_queue_latency_excludes_wip_and_future_scheduled():
    r = Redis.from_url(redis_url)
    now = time.time()

    r.zadd(
        "rq:scheduled:default",
        {
            "due-job": now - 40,
            "future-job": now + 200,
        },
    )
    r.zadd("rq:wip:default", {"working-job": now - 500})
    r.sadd("rq:queues", "rq:queue:default")

    assert job_queue_latency("default", redis_url=redis_url) == pytest.approx(
        40, abs=1
    )
    assert job_queue_latency(redis_url=redis_url) == pytest.approx(40, abs=1)


def test_job_queue_size_and_latency_waiting_only_mixed():
    """Live + due only when WIP and future are also present."""
    r = Redis.from_url(redis_url)
    now = time.time()

    r.rpush("rq:queue:default", "live-a", "live-b")
    r.sadd("rq:queues", "rq:queue:default")
    r.zadd(
        "rq:scheduled:default",
        {
            "due-a": now - 30,
            "future-a": now + 60,
        },
    )
    r.zadd("rq:wip:default", {"wip-a": now - 5, "wip-b": now - 1})

    assert job_queue_size("default", redis_url=redis_url) == 3
    assert job_queue_latency("default", redis_url=redis_url) == pytest.approx(
        30, abs=1
    )
