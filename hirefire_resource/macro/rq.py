import asyncio
import functools
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
    """Maximum job queue latency across the given RQ queues (waiting only).

    With no queues, measures latency across every queue present. Waiting is ready
    queue jobs plus **due** scheduled jobs (``rq:scheduled:{name}`` score ≤ now).
    Delayed retries with an interval use that same scheduled registry (RQ has no
    separate retry set). Future scheduled jobs, started/WIP jobs
    (``rq:wip:{name}``), failed, and deferred jobs are ignored.
    Redis is chosen from ``redis_url``, then the standard env vars, then a local
    default.

    Args:
        *queues (str): Names of the queues for latency measurement.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        float: The maximum latency in seconds across the specified queues.

    Examples:
        >>> job_queue_latency()
        22.918
        >>> job_queue_latency("default")
        10.172
        >>> job_queue_latency("default", "mailer")
        22.918
        >>> job_queue_latency("default", redis_url="redis://localhost:6379/0")
        10.172
    """
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
    """Async wrapper for :func:`job_queue_latency`.

    Runs the synchronous Redis I/O in a thread pool so it does not block the event loop.

    Args:
        *queues (str): Names of the queues for latency measurement.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        float: The maximum latency in seconds across the specified queues.

    Examples:
        >>> await async_job_queue_latency()
        22.918
        >>> await async_job_queue_latency("default")
        10.172
        >>> await async_job_queue_latency("default", "mailer")
        22.918
        >>> await async_job_queue_latency("default", redis_url="redis://localhost:6379/0")
        10.172
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(job_queue_latency, *queues, redis_url=redis_url)
    return await loop.run_in_executor(None, func)


def job_queue_size(*queues: str, redis_url: str | None = None) -> int:
    """Total waiting job count across the given RQ queues.

    With no queues, measures size across every queue present. Waiting is ready
    queue jobs plus **due** scheduled jobs (``rq:scheduled:{name}`` score ≤ now).
    Delayed retries with an interval use that same scheduled registry (RQ has no
    separate retry set). Future scheduled jobs, started/WIP jobs
    (``rq:wip:{name}``), failed, and deferred jobs are excluded.
    Redis is chosen from ``redis_url``, then the standard env vars, then a local
    default.

    Args:
        *queues (str): Names of the queues for size measurement.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        int: The cumulative job queue size across the specified queues.

    Examples:
        >>> job_queue_size()
        127
        >>> job_queue_size("default")
        42
        >>> job_queue_size("default", "mailer")
        127
        >>> job_queue_size("default", redis_url="redis://localhost:6379/0")
        42
    """
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
    """Async wrapper for :func:`job_queue_size`.

    Runs the synchronous Redis I/O in a thread pool so it does not block the event loop.

    Args:
        *queues (str): Names of the queues for size measurement.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        int: The cumulative job queue size across the specified queues.

    Examples:
        >>> await async_job_queue_size()
        127
        >>> await async_job_queue_size("default")
        42
        >>> await async_job_queue_size("default", "mailer")
        127
        >>> await async_job_queue_size("default", redis_url="redis://localhost:6379/0")
        42
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(job_queue_size, *queues, redis_url=redis_url)
    return await loop.run_in_executor(None, func)


def job_queue_working(*queues: str, redis_url: str | None = None) -> int:
    """In-flight (working) job count across the given RQ queues.

    Counts members of ``rq:wip:{name}`` (ZCARD). Empty queue list measures every
    queue present in ``rq:queues``. Never folded into JQL/JQS waiting samples.
    Plan path records this under nested strategy ``wrk``.

    Args:
        *queues (str): Names of the queues for the working count.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        int: Cumulative in-flight job count across the specified queues.

    Examples:
        >>> job_queue_working()
        3
        >>> job_queue_working("default")
        1
        >>> job_queue_working("default", "mailer")
        3
    """
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
    """Async wrapper for :func:`job_queue_working`."""
    loop = asyncio.get_event_loop()
    func = functools.partial(job_queue_working, *queues, redis_url=redis_url)
    return await loop.run_in_executor(None, func)


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
        return value.decode("utf-8")
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
