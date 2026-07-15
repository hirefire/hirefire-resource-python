import asyncio
import functools
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar, cast

from celery import Celery
from celery.signals import before_task_publish
from dateutil import parser
from dateutil.parser import parse
from kombu.exceptions import OperationalError

try:
    from amqp.exceptions import ChannelError

    AMQP_AVAILABLE = True
except ImportError:
    # Fallback so `except ChannelError` still binds when amqp is absent. mypy sees
    # this as redefining the optional import above, which is the intent.
    class ChannelError(Exception):  # type: ignore[no-redef]
        pass

    AMQP_AVAILABLE = False

from hirefire_resource.errors import MissingQueueError


def _get_queue_arguments_from_app(app: Any, queues: tuple[str, ...]) -> dict[str, Any]:
    """
    Extract queue arguments from Celery app configuration for specified queues.

    Args:
        app: Celery app instance with task_queues configuration
        queues: List of queue names to extract arguments for

    Returns:
        dict: Mapping of queue name to queue_arguments dict
    """
    queue_args = {}
    task_queues = getattr(app.conf, "task_queues", None) or []
    for q in task_queues:
        queue_name = getattr(q, "name", None)
        if queue_name and queue_name in queues:
            queue_args[queue_name] = getattr(q, "queue_arguments", None)
    return queue_args


_F = TypeVar("_F", bound=Callable[..., Any])


def mitigate_connection_reset_error(
    retries: int = 10, delay: int = 1
) -> Callable[[_F], _F]:
    """Internal retry helper for Celery macros on ``ConnectionResetError``.

    Retries the wrapped call up to ``retries`` times with a fixed ``delay`` between
    attempts, then re-raises. Not part of the supported public API.

    Args:
        retries (int): Number of retry attempts for connection errors.
        delay (int): Fixed delay between retry attempts in seconds.
    """

    def decorator(func: _F) -> _F:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except ConnectionResetError:
                    if attempt < retries - 1:
                        time.sleep(delay)
                    else:
                        raise

        return cast(_F, wrapper)

    return decorator


@mitigate_connection_reset_error()
def job_queue_latency(*queues: str, broker_url: str | None = None) -> float:
    """Maximum job queue latency across the given Celery queues (Redis or RabbitMQ).

    The broker is chosen from ``broker_url``, then the standard env vars, then a local
    default (AMQP when available, otherwise Redis).

    Note:
        - Due to Celery's architecture, it is not possible to measure job queue latency with 100%
          accuracy. This function attempts to measure latency as accurately as possible, but there
          are some caveats. See the remaining notes for more details.
        - The `run_at` header is added to each task at publish time using a Celery signal. This
          signal is automatically registered when importing this module, so ensure that every Python
          process that enqueues tasks imports this module.
        - Job queue latency is measured by inspecting the `run_at` header of the next job in the
          queue. For Redis, this works fine, as the queue can be inspected without mutation. For
          RabbitMQ, however, this involves consuming a task, and then rejecting and requeuing it.
          This will occasionally lead to out-of-order execution for certain tasks. However, if
          autoscaling is working effectively, this should not be a significant issue.
        - It is recommended to avoid using the eta and countdown options for tasks in queues that
          are being autoscaled. Tasks with an eta (scheduled tasks) are placed in the same queue as
          regular tasks and maintain the FIFO order, which interferes with job queue latency
          measurement.
        - Failed tasks that are to be retried are published with an eta. While not ideal, occasional
          scheduled tasks, which are typically rare, generally aren't an issue.
        - If you absolutely require the ability to schedule tasks to run in the future, consider
          using a workaround, such as a separate queue for scheduled tasks that forwards tasks ready
          to run to the relevant regular queues. Just remember that the `run_at` header is required.

    Args:
        *queues (str): Names of the queues for latency measurement.
        broker_url (str, optional): The broker URL. Defaults in the following order:
            - Passed argument `broker_url`.
            - Environment variables `AMQP_URL`, `RABBITMQ_URL`, `RABBITMQ_BIGWIG_URL`,
              `CLOUDAMQP_URL`, `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`,
              `OPENREDIS_URL`.
            - "amqp://guest:guest@localhost:5672" if AMQP is available, otherwise
              "redis://localhost:6379/0".

    Returns:
        float: The maximum latency in seconds across the specified queues. Returns 0 on
            ``OperationalError`` (for example the broker is unreachable).
            ``ConnectionResetError`` is retried then re-raised.

    Raises:
        MissingQueueError: If no queue names are provided.

    Examples:
        >>> job_queue_latency("celery")
        10.172
        >>> job_queue_latency("celery", "mailer")
        22.918
        >>> job_queue_latency("celery", broker_url="amqp://guest:guest@localhost:5672")
        10.172
        >>> job_queue_latency("celery", "mailer", broker_url="redis://localhost:6379/0")
        22.918
    """
    if not queues:
        raise MissingQueueError()

    broker_url = (
        broker_url
        or os.environ.get("AMQP_URL")
        or os.environ.get("RABBITMQ_URL")
        or os.environ.get("RABBITMQ_BIGWIG_URL")
        or os.environ.get("CLOUDAMQP_URL")
        or os.environ.get("REDIS_TLS_URL")
        or os.environ.get("REDIS_URL")
        or os.environ.get("REDISTOGO_URL")
        or os.environ.get("REDISCLOUD_URL")
        or os.environ.get("OPENREDIS_URL")
    )

    if not broker_url:
        if AMQP_AVAILABLE:
            broker_url = "amqp://guest:guest@localhost:5672"
        else:
            broker_url = "redis://localhost:6379/0"

    app = Celery(broker=broker_url)

    try:
        with app.connection_or_acquire() as connection:
            with connection.channel() as channel:
                if hasattr(channel, "_size"):
                    fn = _job_queue_latency_redis
                else:
                    fn = _job_queue_latency_rabbitmq

                return max(fn(channel, queue) for queue in queues)

    except OperationalError:
        return 0


async def async_job_queue_latency(*queues: str, broker_url: str | None = None) -> float:
    """Async wrapper for :func:`job_queue_latency`.

    Runs the synchronous Celery I/O in a thread pool so it does not block the event loop.

    Note:
        - Due to Celery's architecture, it is not possible to measure job queue latency with 100%
          accuracy. This function attempts to measure latency as accurately as possible, but there
          are some caveats. See the remaining notes for more details.
        - The `run_at` header is added to each task at publish time using a Celery signal. This
          signal is automatically registered when importing this module, so ensure that every Python
          process that enqueues tasks imports this module.
        - Job queue latency is measured by inspecting the `run_at` header of the next job in the
          queue. For Redis, this works fine, as the queue can be inspected without mutation. For
          RabbitMQ, however, this involves consuming a task, and then rejecting and requeuing it.
          This will occasionally lead to out-of-order execution for certain tasks. However, if
          autoscaling is working effectively, this should not be a significant issue.
        - It is recommended to avoid using the eta and countdown options for tasks in queues that
          are being autoscaled. Tasks with an eta (scheduled tasks) are placed in the same queue as
          regular tasks and maintain the FIFO order, which interferes with job queue latency
          measurement.
        - Failed tasks that are to be retried are published with an eta. While not ideal, occasional
          scheduled tasks, which are typically rare, generally aren't an issue.
        - If you absolutely require the ability to schedule tasks to run in the future, consider
          using a workaround, such as a separate queue for scheduled tasks that forwards tasks ready
          to run to the relevant regular queues. Just remember that the `run_at` header is required.

    Args:
        *queues (str): Names of the queues for latency measurement.
        broker_url (str, optional): The broker URL. Defaults in the following order:
            - Passed argument `broker_url`.
            - Environment variables `AMQP_URL`, `RABBITMQ_URL`, `RABBITMQ_BIGWIG_URL`,
              `CLOUDAMQP_URL`, `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`,
              `OPENREDIS_URL`.
            - "amqp://guest:guest@localhost:5672" if AMQP is available, otherwise
              "redis://localhost:6379/0".

    Returns:
        float: The maximum latency in seconds across the specified queues. Returns 0 on
            ``OperationalError`` (for example the broker is unreachable).
            ``ConnectionResetError`` is retried then re-raised.

    Raises:
        MissingQueueError: If no queue names are provided.

    Examples:
        >>> await async_job_queue_latency("celery")
        10.172
        >>> await async_job_queue_latency("celery", "mailer")
        22.918
        >>> await async_job_queue_latency("celery", broker_url="amqp://guest:guest@localhost:5672")
        10.172
        >>> await async_job_queue_latency("celery", "mailer", broker_url="redis://localhost:6379/0")
        22.918
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(job_queue_latency, *queues, broker_url=broker_url)
    return await loop.run_in_executor(None, func)


@mitigate_connection_reset_error()
def job_queue_size(
    *queues: str,
    broker_url: str | None = None,
    celery_app: "Celery | None" = None,
) -> int:
    """Total job count across the given Celery queues (Redis or RabbitMQ).

    Sums broker backlog plus worker-held work for those queues (active, reserved, and
    due scheduled tasks from Celery inspect). The broker is chosen from ``broker_url``
    or ``celery_app``, then the standard env vars, then a local default (AMQP when
    available, otherwise Redis).

    Note:
        - It is recommended to avoid using the eta and countdown options for tasks in queues that
          are being autoscaled. Tasks with an eta (scheduled tasks) are placed in the same queue as
          regular tasks, which interferes with job queue size measurement.
        - Failed tasks that are to be retried are published with an eta. While not ideal, occasional
          scheduled tasks, which are typically rare, generally aren't an issue.
        - If you absolutely require the ability to schedule tasks to run in the future, consider
          using a workaround, such as a separate queue for scheduled tasks that forwards tasks ready
          to run to the relevant regular queues. When using RabbitMQ (AMQP), consider using the
          Delayed Message Plugin.
        - For RabbitMQ queues with custom arguments (e.g., x-max-priority for priority queues),
          pass your configured Celery app via the `celery_app` parameter. This allows the function
          to extract and use the correct queue arguments when querying RabbitMQ.

    Args:
        *queues (str): Names of the queues for size measurement.
        broker_url (str, optional): The broker URL. Cannot be used together with `celery_app`.
            Defaults in the following order:
            - Passed argument `broker_url`.
            - Environment variables `AMQP_URL`, `RABBITMQ_URL`, `RABBITMQ_BIGWIG_URL`,
              `CLOUDAMQP_URL`, `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`,
              `OPENREDIS_URL`.
            - "amqp://guest:guest@localhost:5672" if AMQP is available, otherwise
              "redis://localhost:6379/0".
        celery_app (Celery, optional): A configured Celery app instance. Cannot be used together
            with `broker_url`. When provided, the function uses this app's connection and extracts
            queue arguments from celery_app.conf.task_queues. This is required for RabbitMQ queues
            with custom arguments like x-max-priority.

    Returns:
        int: Broker depth plus worker-held tasks across the specified queues. Returns 0
            on ``OperationalError`` (for example the broker is unreachable).
            ``ConnectionResetError`` is retried then re-raised.

    Raises:
        MissingQueueError: If no queue names are provided.
        ValueError: If both `broker_url` and `celery_app` are provided.

    Examples:
        >>> job_queue_size("celery")
        42
        >>> job_queue_size("celery", "mailer")
        85
        >>> job_queue_size("celery", broker_url="amqp://user:password@host:5672")
        42
        >>> job_queue_size("celery", broker_url="redis://localhost:6379/0")
        42
        >>> # For priority queues, pass your configured Celery app:
        >>> job_queue_size("celery", celery_app=celery_app)
        42
    """
    if not queues:
        raise MissingQueueError()

    if celery_app is not None and broker_url is not None:
        raise ValueError(
            "Cannot specify both 'celery_app' and 'broker_url'. "
            "Use 'celery_app' to pass your configured Celery app (recommended for priority queues), "
            "or 'broker_url' for simple setups."
        )

    if celery_app is None:
        broker_url = (
            broker_url
            or os.environ.get("AMQP_URL")
            or os.environ.get("RABBITMQ_URL")
            or os.environ.get("RABBITMQ_BIGWIG_URL")
            or os.environ.get("CLOUDAMQP_URL")
            or os.environ.get("REDIS_TLS_URL")
            or os.environ.get("REDIS_URL")
            or os.environ.get("REDISTOGO_URL")
            or os.environ.get("REDISCLOUD_URL")
            or os.environ.get("OPENREDIS_URL")
        )

        if not broker_url:
            if AMQP_AVAILABLE:
                broker_url = "amqp://guest:guest@localhost:5672"
            else:
                broker_url = "redis://localhost:6379/0"

        celery_app = Celery(broker=broker_url)

    try:
        with celery_app.connection_or_acquire() as connection:
            with connection.channel() as channel:
                worker_task_count = _job_queue_size_worker(celery_app, queues)
                broker_task_count = _job_queue_size_broker(celery_app, channel, queues)
                return worker_task_count + broker_task_count

    except OperationalError:
        return 0


async def async_job_queue_size(
    *queues: str,
    broker_url: str | None = None,
    celery_app: "Celery | None" = None,
) -> int:
    """Async wrapper for :func:`job_queue_size`.

    Runs the synchronous Celery I/O in a thread pool so it does not block the event loop.

    Note:
        - It is recommended to avoid using the eta and countdown options for tasks in queues that
          are being autoscaled. Tasks with an eta (scheduled tasks) are placed in the same queue as
          regular tasks, which interferes with job queue size measurement.
        - Failed tasks that are to be retried are published with an eta. While not ideal, occasional
          scheduled tasks, which are typically rare, generally aren't an issue.
        - If you absolutely require the ability to schedule tasks to run in the future, consider
          using a workaround, such as a separate queue for scheduled tasks that forwards tasks ready
          to run to the relevant regular queues. When using RabbitMQ (AMQP), consider using the
          Delayed Message Plugin.
        - For RabbitMQ queues with custom arguments (e.g., x-max-priority for priority queues),
          pass your configured Celery app via the `celery_app` parameter.

    Args:
        *queues (str): Names of the queues for size measurement.
        broker_url (str, optional): The broker URL. Cannot be used together with `celery_app`.
            Defaults in the following order:
            - Passed argument `broker_url`.
            - Environment variables `AMQP_URL`, `RABBITMQ_URL`, `RABBITMQ_BIGWIG_URL`,
              `CLOUDAMQP_URL`, `REDIS_TLS_URL`, `REDIS_URL`, `REDISTOGO_URL`, `REDISCLOUD_URL`,
              `OPENREDIS_URL`.
            - "amqp://guest:guest@localhost:5672" if AMQP is available, otherwise
              "redis://localhost:6379/0".
        celery_app (Celery, optional): A configured Celery app instance. Cannot be used together
            with `broker_url`. When provided, the function uses this app's connection and extracts
            queue arguments from celery_app.conf.task_queues. This is required for RabbitMQ
            queues with custom arguments like x-max-priority.

    Returns:
        int: Broker depth plus worker-held tasks across the specified queues. Returns 0
            on ``OperationalError`` (for example the broker is unreachable).
            ``ConnectionResetError`` is retried then re-raised.

    Raises:
        MissingQueueError: If no queue names are provided.
        ValueError: If both `broker_url` and `celery_app` are provided.

    Examples:
        >>> await async_job_queue_size("celery")
        42
        >>> await async_job_queue_size("celery", "mailer")
        85
        >>> await async_job_queue_size("celery", broker_url="amqp://user:password@host:5672")
        42
        >>> await async_job_queue_size("celery", celery_app=celery_app)
        42
    """
    loop = asyncio.get_event_loop()
    func = functools.partial(
        job_queue_size, *queues, broker_url=broker_url, celery_app=celery_app
    )
    return await loop.run_in_executor(None, func)


@before_task_publish.connect
def run_at_header_signal(
    sender: Any = None,
    headers: Any = None,
    body: Any = None,
    properties: Any = None,
    **kwargs: Any,
) -> None:
    """Celery ``before_task_publish`` handler that sets the ``run_at`` task header.

    Connected automatically when this module is imported. Sets ``run_at`` from the
    task's ``eta`` when present, otherwise the current UTC time. Required for
    :func:`job_queue_latency`. Callers do not invoke this directly.
    """
    headers = headers or {}
    eta = headers.get("eta")

    if eta:
        headers["run_at"] = eta
    else:
        headers["run_at"] = datetime.now(timezone.utc).isoformat()


def _job_queue_latency_redis(channel: Any, queue: str) -> float:
    oldest_job = channel.client.lindex(queue, -1)

    if oldest_job:
        oldest_job = json.loads(_as_str(oldest_job))
        run_at = oldest_job.get("headers", {}).get("run_at")

        if run_at:
            run_at_time = parse(run_at)
            latency = time.time() - run_at_time.timestamp()
            return max(0, latency)

    return 0


def _as_str(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _job_queue_latency_rabbitmq(channel: Any, queue: str) -> float:
    try:
        message = channel.basic_get(queue)

        if message is None:
            return 0

        run_at = message.headers.get("run_at")

        if run_at:
            run_at_time = parse(run_at)
            latency = time.time() - run_at_time.timestamp()
            result = max(0, latency)
        else:
            result = 0

        channel.basic_reject(message.delivery_tag, requeue=True)

        return result
    except ChannelError:
        return 0


def _job_queue_size_worker(app: Any, queues: tuple[str, ...]) -> int:
    worker_data = _worker_data(app)
    return sum(worker_data.get(queue, 0) for queue in queues)


def _job_queue_size_broker(app: Any, channel: Any, queues: tuple[str, ...]) -> int:
    if hasattr(channel, "_size"):
        return sum(_job_queue_size_redis(channel, queue) for queue in queues)
    else:
        queue_args = _get_queue_arguments_from_app(app, queues)
        return sum(
            _job_queue_size_rabbitmq(channel, queue, queue_args.get(queue))
            for queue in queues
        )


def _job_queue_size_redis(channel: Any, queue: str) -> int:
    return channel.client.llen(queue)


def _job_queue_size_rabbitmq(channel: Any, queue: str, arguments: Any = None) -> int:
    try:
        return channel.queue_declare(
            queue=queue, passive=True, arguments=arguments
        ).message_count
    except ChannelError:
        return 0


_worker_data_cache_enabled = True
_worker_data_cache_value: dict[str, int] | None = None
_worker_data_cache_time = time.time() - (5 + 1)


def _cache_worker_data(enabled: bool) -> None:
    global _worker_data_cache_enabled
    _worker_data_cache_enabled = enabled


def _worker_data(app: Any) -> dict[str, int]:
    global _worker_data_cache_value, _worker_data_cache_time

    if not _worker_data_cache_enabled or (_worker_data_cache_time + 5) < time.time():
        app.conf.control_queue_exclusive = True
        app.conf.event_queue_exclusive = True
        i = app.control.inspect()
        now = time.time()
        queue_info: dict[str, int] = {}

        for collection in [i.active(), i.reserved(), i.scheduled()]:
            if collection is not None:
                for worker, tasks in collection.items():
                    for task in tasks:
                        task_info = task

                        if task.get("eta"):
                            eta_string = task.get("eta")
                            eta_datetime = parser.parse(eta_string)
                            eta_timestamp = eta_datetime.timestamp()

                            if now < eta_timestamp:
                                continue

                            task_info = task["request"]

                        queue = task_info["delivery_info"]["routing_key"]

                        if queue not in queue_info:
                            queue_info[queue] = 0

                        queue_info[queue] += 1

        _worker_data_cache_value = queue_info
        _worker_data_cache_time = time.time()

    # The first call always populates the cache (it starts stale), so by here the
    # value is a dict, never the initial None.
    return cast("dict[str, int]", _worker_data_cache_value)
