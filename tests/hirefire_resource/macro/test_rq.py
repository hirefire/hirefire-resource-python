import os
import time
from datetime import datetime, timezone

import pytest
from freezegun import freeze_time
from redis import Redis
from rq import Queue

from hirefire_resource import HireFire, plan
from hirefire_resource.macro.rq import (
    _is_due_scheduled_score,
    _resolve_redis_url,
    async_job_queue_latency,
    async_job_queue_size,
    async_job_queue_working,
    job_queue_latency,
    job_queue_size,
    job_queue_working,
)

redis_url = f"redis://localhost:{os.environ.get('REDIS_PORT', '6379')}/0"
queue_name = "default"


def test_library_loaded_is_true_when_rq_package_is_imported():
    assert plan.library_loaded("rq")
    assert plan.executable("rq")
    assert plan.any_allowlisted_job_queue_library_loaded()


@pytest.fixture(autouse=True)
def clear_redis():
    r = Redis.from_url(redis_url)
    r.flushdb()


def test_job_queue_latency_default_redis_url():
    assert job_queue_latency("test_default_redis_url") == 0


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

    assert job_queue_latency("default", redis_url=redis_url) == pytest.approx(40, abs=1)
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
    assert job_queue_latency("default", redis_url=redis_url) == pytest.approx(30, abs=1)


def test_job_queue_working_idle_is_zero():
    assert job_queue_working(redis_url=redis_url) == 0
    assert job_queue_working("default", redis_url=redis_url) == 0


def test_job_queue_working_counts_in_flight_and_filters_queues():
    r = Redis.from_url(redis_url)
    now = time.time()
    r.sadd("rq:queues", "rq:queue:default", "rq:queue:mailer", "rq:queue:critical")
    r.zadd("rq:wip:default", {"w1": now - 1})
    r.zadd("rq:wip:mailer", {"w2": now - 2, "w3": now - 3})
    r.rpush("rq:queue:default", "live-1")

    assert job_queue_working(redis_url=redis_url) == 3
    assert job_queue_working("default", redis_url=redis_url) == 1
    assert job_queue_working("mailer", redis_url=redis_url) == 2
    assert job_queue_working("critical", redis_url=redis_url) == 0
    assert job_queue_working("default", "mailer", redis_url=redis_url) == 3
    assert job_queue_size("default", redis_url=redis_url) == 1
    assert job_queue_size("mailer", redis_url=redis_url) == 0


@pytest.mark.asyncio
async def test_async_job_queue_working():
    r = Redis.from_url(redis_url)
    r.sadd("rq:queues", "rq:queue:default")
    r.zadd("rq:wip:default", {"w1": time.time()})
    assert await async_job_queue_working(redis_url=redis_url) == 1
    assert await async_job_queue_working("default", redis_url=redis_url) == 1
    assert await async_job_queue_working("critical", redis_url=redis_url) == 0


def test_plan_execute_rq_jqs_also_samples_wrk(monkeypatch):
    monkeypatch.setenv("HIREFIRE_RQ_URL", redis_url)
    r = Redis.from_url(redis_url)
    now = time.time()
    r.sadd("rq:queues", "rq:queue:default")
    r.rpush("rq:queue:default", "live-1")
    r.zadd("rq:wip:default", {"w1": now - 1, "w2": now - 2})

    HireFire.configuration.buffer.flush()
    plan.execute(
        {
            "name": "worker",
            "adapter": "rq",
            "strategy": "jqs",
            "queues": ["default"],
            "options": {},
        }
    )

    flushed = HireFire.configuration.buffer.flush()
    assert "worker" in flushed
    assert "jqs" in flushed["worker"]
    assert "wrk" in flushed["worker"]
    jqs_value = list(flushed["worker"]["jqs"].values())[-1]
    wrk_value = list(flushed["worker"]["wrk"].values())[-1]
    assert jqs_value == job_queue_size("default", redis_url=redis_url)
    assert wrk_value == job_queue_working("default", redis_url=redis_url)
    assert wrk_value == 2
    assert jqs_value == 1


def test_plan_execute_rq_jql_also_samples_wrk(monkeypatch):
    monkeypatch.setenv("HIREFIRE_RQ_URL", redis_url)
    r = Redis.from_url(redis_url)
    r.sadd("rq:queues", "rq:queue:default")
    r.zadd("rq:wip:default", {"w1": time.time()})

    HireFire.configuration.buffer.flush()
    plan.execute(
        {
            "name": "worker",
            "adapter": "rq",
            "strategy": "jql",
            "queues": ["default"],
            "options": {},
        }
    )

    flushed = HireFire.configuration.buffer.flush()
    assert "jql" in flushed["worker"]
    assert list(flushed["worker"]["wrk"].values())[-1] == 1


def test_plan_execute_rq_empty_queues_samples_all_wrk(monkeypatch):
    monkeypatch.setenv("HIREFIRE_RQ_URL", redis_url)
    r = Redis.from_url(redis_url)
    now = time.time()
    r.sadd("rq:queues", "rq:queue:default", "rq:queue:mailer")
    r.zadd("rq:wip:default", {"w1": now})
    r.zadd("rq:wip:mailer", {"w2": now})

    HireFire.configuration.buffer.flush()
    plan.execute(
        {
            "name": "worker",
            "adapter": "rq",
            "strategy": "jqs",
            "queues": [],
            "options": {},
        }
    )

    flushed = HireFire.configuration.buffer.flush()
    wrk_value = list(flushed["worker"]["wrk"].values())[-1]
    assert wrk_value == 2
    assert wrk_value == job_queue_working(redis_url=redis_url)


def _clear_redis_env(monkeypatch):
    for key in (
        "HIREFIRE_RQ_URL",
        "REDIS_TLS_URL",
        "REDIS_URL",
        "REDISTOGO_URL",
        "REDISCLOUD_URL",
        "OPENREDIS_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolve_redis_url_multi_key_precedence(monkeypatch):
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("OPENREDIS_URL", "redis://openredis/0")
    monkeypatch.setenv("REDISCLOUD_URL", "redis://rediscloud/0")
    monkeypatch.setenv("REDISTOGO_URL", "redis://redistogo/0")
    monkeypatch.setenv("REDIS_URL", "redis://redis-url/0")
    monkeypatch.setenv("REDIS_TLS_URL", "rediss://redis-tls/0")
    monkeypatch.setenv("HIREFIRE_RQ_URL", "redis://hirefire/0")
    assert _resolve_redis_url(None) == "rediss://redis-tls/0"

    monkeypatch.delenv("REDIS_TLS_URL")
    assert _resolve_redis_url(None) == "redis://redis-url/0"


def test_resolve_redis_url_explicit_wins_over_env(monkeypatch):
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://from-env/0")
    assert _resolve_redis_url("redis://explicit/0") == "redis://explicit/0"


def test_hirefire_rq_url_is_plan_only(monkeypatch):
    import hirefire_resource.macro.rq as rq_macro

    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("HIREFIRE_RQ_URL", "redis://hf/1")
    monkeypatch.setenv("REDIS_URL", "redis://local/0")
    assert _resolve_redis_url(None) == "redis://local/0"
    assert rq_macro.plan_connection_options() == {"redis_url": "redis://hf/1"}
