import asyncio
import functools
import os
import time
from datetime import datetime

import redis

from hirefire_resource.errors import MissingQueueError


def job_queue_latency(*queues, redis_url=None):
    """
    Calculates the maximum latency across the specified queues using RQ with Redis as the broker.

    This function dynamically selects the Redis broker based on the provided redis_url or environment
    variables, or falls back to a default local Redis URL.

     Args:
        *queues (str): The names of the queues to be included in the measurement of job queue latency.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_URL`, `REDIS_TLS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        float: The maximum job queue latency in seconds across the specified queues with sub-second precision.

    Raises:
        MissingQueueError: If no queue names are provided.

    Examples:
        >>> job_queue_latency("default")
        10.172
        >>> job_queue_latency("default", "mailer")
        22.918
        >>> job_queue_latency("default", redis_url="redis://localhost:6379/0")
        15.234
    """
    if not queues:
        raise MissingQueueError()

    redis_url = (
        redis_url
        or os.getenv("REDIS_URL")
        or os.getenv("REDIS_TLS_URL")
        or os.getenv("REDISTOGO_URL")
        or os.getenv("REDISCLOUD_URL")
        or os.getenv("OPENREDIS_URL")
        or "redis://localhost:6379/0"
    )
    r = redis.Redis.from_url(redis_url)
    current_time = time.time()
    pipe = r.pipeline()

    for queue_name in queues:
        pipe.lindex("rq:queue:" + queue_name, 0)
        pipe.zrangebyscore(
            "rq:scheduled:" + queue_name,
            "-inf",
            current_time,
            withscores=True,
            start=0,
            num=1,
        )

    job_ids = pipe.execute()

    for job_id in job_ids[::2]:
        if job_id:
            pipe.hget("rq:job:" + job_id.decode("utf-8"), "enqueued_at")

    enqueued_at_times = pipe.execute()

    max_latency = 0

    for enqueued_at in enqueued_at_times:
        if enqueued_at:
            latency = current_time - _iso_to_unix(enqueued_at.decode("utf-8"))
            max_latency = max(max_latency, latency)

    for job_data in job_ids[1::2]:
        if job_data:
            job_id, score = job_data[0]
            if score < current_time:
                latency = current_time - score
                max_latency = max(max_latency, latency)

    return max_latency


async def async_job_queue_latency(*queues, redis_url=None):
   """
   Asynchronously calculates the maximum job queue latency across the specified queues using RQ
   with Redis as the broker.

   This function is an asynchronous wrapper around the synchronous `job_queue_latency` function. It
   executes the synchronous function in a separate thread using asyncio's event loop and
   `run_in_executor` method. This ensures that the synchronous Redis I/O operations do not block
   the asyncio event loop.

   Args:
       *queues (str): The names of the queues to be included in the measurement of job queue latency.
       redis_url (str, optional): The Redis URL. Defaults in the following order:
           - Passed argument `redis_url`.
           - Environment variables `REDIS_URL`, `REDIS_TLS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`, `OPENREDIS_URL`.
           - "redis://localhost:6379/0".

   Returns:
       float: The maximum job queue latency in seconds across the specified queues with sub-second precision.

   Raises:
       MissingQueueError: If no queue names are provided.

   Examples:
       >>> await async_job_queue_latency("default")
       10.172
       >>> await async_job_queue_latency("default", "mailer")
       22.918
       >>> await async_job_queue_latency("default", redis_url="redis://localhost:6379/0")
       15.234
   """
   loop = asyncio.get_event_loop()
   func = functools.partial(job_queue_latency, *queues, redis_url=redis_url)
   return await loop.run_in_executor(None, func)


def job_queue_size(*queues, redis_url=None):
    """
    Calculates the total job queue size across the specified queues using RQ with Redis as the broker.

    This function dynamically selects the Redis broker based on the provided redis_url, environment
    variables, or falls back to a default local Redis URL.

    Args:
        *queues (str): The names of the queues to be included in the measurement of job queue size.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_URL`, `REDIS_TLS_URL`, `REDISTOGO_URL`,
              `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        int: The cumulative job queue size across the specified queues.

    Raises:
        MissingQueueError: If no queue names are provided.

    Examples:
        >>> job_queue_size("default")
        42
        >>> job_queue_size("default", "mailer")
        85
        >>> job_queue_size("default", redis_url="redis://localhost:6379/0")
        57
    """
    if not queues:
        raise MissingQueueError()

    redis_url = (
        redis_url
        or os.getenv("REDIS_URL")
        or os.getenv("REDIS_TLS_URL")
        or os.getenv("REDISTOGO_URL")
        or os.getenv("REDISCLOUD_URL")
        or os.getenv("OPENREDIS_URL")
        or "redis://localhost:6379/0"
    )
    r = redis.Redis.from_url(redis_url)
    current_time = int(time.time())
    pipe = r.pipeline()

    for queue_name in queues:
        ready_queue_key = f"rq:queue:{queue_name}"
        scheduled_queue_key = f"rq:scheduled:{queue_name}"
        pipe.llen(ready_queue_key)
        pipe.zcount(scheduled_queue_key, 0, current_time)

    job_counts = pipe.execute()
    total_jobs = sum(job_counts)

    return total_jobs


async def async_job_queue_size(*queues, redis_url=None):
    """
    Asynchronously calculates the total job queue size across the specified queues using RQ
    with Redis as the broker.

    This function is an asynchronous wrapper around the synchronous `job_queue_size` function. It
    executes the synchronous function in a separate thread using asyncio's event loop and
    `run_in_executor` method. This ensures that the synchronous Redis I/O operations do not block
    the asyncio event loop.

    Args:
        *queues (str): The names of the queues to be included in the measurement of job queue size.
        redis_url (str, optional): The Redis URL. Defaults in the following order:
            - Passed argument `redis_url`.
            - Environment variables `REDIS_URL`, `REDIS_TLS_URL`, `REDISTOGO_URL`,
              `REDISCLOUD_URL`, `OPENREDIS_URL`.
            - "redis://localhost:6379/0".

    Returns:
        int: The cumulative job queue size across the specified queues.

    Raises:
        MissingQueueError: If no queue names are provided.

    Examples:
        >>> await async_job_queue_size("default")
        42
        >>> await async_job_queue_size("default", "mailer")
        85
        >>> await async_job_queue_size("default", redis_url="redis://localhost:6379/0")
        57
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(job_queue_size, *queues, redis_url=redis_url)
    return await loop.run_in_executor(None, func)


def _iso_to_unix(iso_time):
    dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    unix_time = float(dt.timestamp())

    return unix_time
