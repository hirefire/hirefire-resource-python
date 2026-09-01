import asyncio
import os
import time
from datetime import datetime
from typing import Any

import redis

from hirefire_resource.plan import hooks as _plan_hooks
from hirefire_resource.utility import normalize_queues

before_sample_job_queues = _plan_hooks.before_sample_job_queues
after_sample_job_queues = _plan_hooks.after_sample_job_queues
reinit_after_fork = _plan_hooks.reinit_after_fork

_SAMPLE_REDIS_TIMEOUT = 5.0


def _resolve_redis_url(redis_url: str | None) -> str:
    return (
        redis_url
        or os.getenv("REDIS_TLS_URL")
        or os.getenv("REDIS_URL")
        or os.getenv("REDISTOGO_URL")
        or os.getenv("REDISCLOUD_URL")
        or os.getenv("OPENREDIS_URL")
        or "redis://localhost:6379/0"
    )


def _open_redis(redis_url: str) -> redis.Redis:
    return redis.Redis.from_url(
        redis_url,
        socket_timeout=_SAMPLE_REDIS_TIMEOUT,
        socket_connect_timeout=_SAMPLE_REDIS_TIMEOUT,
    )


def job_queue_latency(*queues: str, redis_url: str | None = None) -> float:
    redis_url = _resolve_redis_url(redis_url)

    redis_client = _open_redis(redis_url)
    try:
        queue_names = normalize_queues(*queues, allow_empty=True)
        if not queue_names:
            queue_names = _registered_queue_names(redis_client)

        pipeline = redis_client.pipeline()
        current_time = time.time()

        for queue in queue_names:
            pipeline.lindex(f"rq:queue:{queue}", 0)
            pipeline.zrangebyscore(
                f"rq:scheduled:{queue}",
                "-inf",
                current_time,
                withscores=True,
                start=0,
                num=1,
            )

        job_ids = pipeline.execute()

        for job_id in job_ids[::2]:
            if job_id:
                pipeline.hget(f"rq:job:{_as_str(job_id)}", "enqueued_at")

        enqueued_at_times = pipeline.execute()

        max_latency = 0.0

        for enqueued_at in enqueued_at_times:
            if not enqueued_at:
                continue
            enqueued_unix = _iso_to_unix(_as_str(enqueued_at))
            if enqueued_unix is None:
                continue
            latency = current_time - enqueued_unix
            max_latency = max(max_latency, latency)

        for job_data in job_ids[1::2]:
            if job_data:
                _job_id, score = job_data[0]
                if _is_due_scheduled_score(score, current_time):
                    latency = current_time - score
                    max_latency = max(max_latency, latency)

        return max_latency
    finally:
        redis_client.close()


async def async_job_queue_latency(*queues: str, redis_url: str | None = None) -> float:
    return await asyncio.to_thread(job_queue_latency, *queues, redis_url=redis_url)


def job_queue_size(*queues: str, redis_url: str | None = None) -> int:
    redis_url = _resolve_redis_url(redis_url)

    redis_client = _open_redis(redis_url)
    try:
        queue_names = normalize_queues(*queues, allow_empty=True)
        if not queue_names:
            queue_names = _registered_queue_names(redis_client)

        pipeline = redis_client.pipeline()
        current_time = time.time()

        for queue in queue_names:
            pipeline.llen(f"rq:queue:{queue}")
            pipeline.zcount(f"rq:scheduled:{queue}", "-inf", current_time)

        job_counts = pipeline.execute()
        return sum(job_counts)
    finally:
        redis_client.close()


async def async_job_queue_size(*queues: str, redis_url: str | None = None) -> int:
    return await asyncio.to_thread(job_queue_size, *queues, redis_url=redis_url)


def job_queue_working(*queues: str, redis_url: str | None = None) -> int:
    redis_url = _resolve_redis_url(redis_url)

    redis_client = _open_redis(redis_url)
    try:
        queue_names = normalize_queues(*queues, allow_empty=True)
        if not queue_names:
            queue_names = _registered_queue_names(redis_client)

        pipeline = redis_client.pipeline()
        for queue in queue_names:
            pipeline.zcard(f"rq:wip:{queue}")

        return sum(pipeline.execute())
    finally:
        redis_client.close()


async def async_job_queue_working(*queues: str, redis_url: str | None = None) -> int:
    return await asyncio.to_thread(job_queue_working, *queues, redis_url=redis_url)


_QUEUE_KEY_PREFIX = "rq:queue:"


def _is_due_scheduled_score(score: float, now: float) -> bool:
    return score <= now


def _registered_queue_names(redis_client: redis.Redis) -> set[str]:
    names: set[str] = set()
    for key in redis_client.smembers("rq:queues"):
        key_str = _as_str(key)
        if key_str.startswith(_QUEUE_KEY_PREFIX):
            names.add(key_str[len(_QUEUE_KEY_PREFIX) :])
    return names


def _as_str(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _iso_to_unix(iso_time: str) -> float | None:
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        return float(dt.timestamp())
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def plan_options(strategy: object, options: object) -> dict[str, Any]:
    return {}


def plan_connection_options() -> dict[str, Any]:
    from hirefire_resource.identity import presence

    url = presence(os.environ.get("HIREFIRE_RQ_URL"))
    return {"redis_url": url} if url else {}


def supports_plan_strategy(strategy: object) -> bool:
    from hirefire_resource import plan

    return plan.known_strategy(strategy)
