import asyncio
import functools
import json
import os
import time
from datetime import datetime, timezone

from celery import Celery
from celery.signals import before_task_publish
from dateutil import parser
from dateutil.parser import parse
from kombu.exceptions import OperationalError

try:
    from amqp.exceptions import ChannelError

    AMQP_AVAILABLE = True
except ImportError:

    class ChannelError(Exception):
        pass

    AMQP_AVAILABLE = False

from hirefire_resource.errors import MissingQueueError


def _get_queue_arguments_from_app(app, queues):
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


def mitigate_connection_reset_error(retries=10, delay=1):
    """
    Decorator to retry a function when ConnectionResetError occurs.

    Args:
        retries (int): Number of retry attempts for connection errors.
        delay (int): Fixed delay between retry attempts in seconds.
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except ConnectionResetError:
                    if attempt < retries - 1:
                        time.sleep(delay)
                    else:
                        raise

        return wrapper

    return decorator


@mitigate_connection_reset_error()
def job_queue_latency(*queues, broker_url=None):
    """
    Calculates the maximum job queue latency across the specified queues using Celery with either
    Redis or RabbitMQ (AMQP) as the broker.

    This function dynamically selects the broker based on the provided broker_url, environment
    variables, or falls back to a default local broker URL. If RabbitMQ (AMQP) is available, it is
    preferred; otherwise, Redis is used.

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
        float: The maximum latency in seconds across the specified queues.

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


async def async_job_queue_latency(*queues, broker_url=None):
    """
    Asynchronously calculates the maximum job queue latency across the specified queues using Celery
    with either Redis or RabbitMQ (AMQP) as the broker.

    This function is an asynchronous wrapper around the synchronous `job_queue_latency` function. It
    executes the synchronous function in a separate thread using asyncio's event loop and
    `run_in_executor` method. This ensures that the synchronous Celery I/O operations do not block
    the asyncio event loop.

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
        float: The maximum latency in seconds across the specified queues.

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
def job_queue_size(*queues, broker_url=None, celery_app=None):
    """
    Calculates the total job queue size across the specified queues using Celery with either Redis
    or RabbitMQ (AMQP) as the broker.

    This function dynamically selects the broker based on the provided broker_url, environment
    variables, or falls back to a default local broker URL. If RabbitMQ (AMQP) is available, it is
    preferred; otherwise, Redis is used.

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
        int: The cumulative job queue size across the specified queues.

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


async def async_job_queue_size(*queues, broker_url=None, celery_app=None):
    """
    Asynchronously calculates the total job queue size across the specified queues using Celery with
    either Redis or RabbitMQ (AMQP) as the broker.

    This function is an asynchronous wrapper around the synchronous `job_queue_size` function. It
    executes the synchronous function in a separate thread using asyncio's event loop and
    `run_in_executor` method. This ensures that the synchronous Celery I/O operations do not block
    the asyncio event loop.

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
            queue arguments from celery_app.conf.task_queues.

    Returns:
        int: The cumulative job queue size across the specified queues.

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
    sender=None, headers=None, body=None, properties=None, **kwargs
):
    headers = headers or {}
    eta = headers.get("eta")

    if eta:
        headers["run_at"] = eta
    else:
        headers["run_at"] = datetime.now(timezone.utc).isoformat()


def _job_queue_latency_redis(channel, queue):
    oldest_job = channel.client.lindex(queue, -1)

    if oldest_job:
        oldest_job = json.loads(oldest_job.decode("utf-8"))
        run_at = oldest_job.get("headers", {}).get("run_at")

        if run_at:
            run_at_time = parse(run_at)
            latency = time.time() - run_at_time.timestamp()
            return max(0, latency)

    return 0


def _job_queue_latency_rabbitmq(channel, queue):
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


def _job_queue_size_worker(app, queues):
    worker_data = _worker_data(app)
    return sum(worker_data.get(queue, 0) for queue in queues)


def _job_queue_size_broker(app, channel, queues):
    if hasattr(channel, "_size"):
        return sum(_job_queue_size_redis(channel, queue) for queue in queues)
    else:
        queue_args = _get_queue_arguments_from_app(app, queues)
        return sum(
            _job_queue_size_rabbitmq(channel, queue, queue_args.get(queue))
            for queue in queues
        )


def _job_queue_size_redis(channel, queue):
    return channel.client.llen(queue)


def _job_queue_size_rabbitmq(channel, queue, arguments=None):
    try:
        return channel.queue_declare(
            queue=queue, passive=True, arguments=arguments
        ).message_count
    except ChannelError:
        return 0


_worker_data_cache_enabled = True
_worker_data_cache_value = None
_worker_data_cache_time = time.time() - (5 + 1)


# Used to disable the worker data cache for testing
def _cache_worker_data(enabled):
    global _worker_data_cache_enabled
    _worker_data_cache_enabled = enabled


def _worker_data(app):
    global _worker_data_cache_value, _worker_data_cache_time

    if not _worker_data_cache_enabled or (_worker_data_cache_time + 5) < time.time():
        i = app.control.inspect()
        now = time.time()
        queue_info = {}

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

    return _worker_data_cache_value
