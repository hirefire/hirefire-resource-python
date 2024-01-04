import json
import os
import time
from datetime import datetime

from celery import Celery
from celery.signals import before_task_publish
from kombu.exceptions import OperationalError

try:
    from amqp.exceptions import ChannelError

    AMQP_AVAILABLE = True
except ImportError:

    class ChannelError(Exception):
        pass

    AMQP_AVAILABLE = False

from hirefire_resource.errors import MissingQueueError


def job_queue_size(*queues, broker_url=None):
    """
    Calculates the total job queue size across the specified queues using Celery with either
    Redis or RabbitMQ (AMQP) as the broker.

    This function dynamically selects the broker based on the provided broker_url, environment variables,
    or falls back to a default local broker URL. If RabbitMQ (AMQP) is available, it is preferred;
    otherwise, Redis is used.

    Args:
        queues (str): A variable number of queue names as strings.
        broker_url (str, optional): The broker URL. Defaults in order:
                                    - Passed argument broker_url.
                                    - Environment variables AMQP_URL, CLOUDAMQP_URL, REDIS_URL, REDIS_TLS_URL.
                                    - "amqp://guest:guest@localhost:5672/" if AMQP is available, otherwise
                                      "redis://localhost:6379/0".

    Returns:
        int: The cumulative job queue size across the specified queues.

    Raises:
        MissingQueueError: If no queue names are provided.

    Examples:
        >>> job_queue_size('celery')
        42
        >>> job_queue_size('celery', 'mailer')
        85
        >>> job_queue_size('celery', broker_url='amqp://user:password@host:5672/vhost')
        30

    Note: @TODO
        - Due to performance concerns, this function does not take into account tasks scheduled to
          run in the future using eta or countdown. Autoscaling queues containing such tasks is not
          recommended. A potential workaround is to create a separate 'scheduled' queue and assign a
          dedicated worker whose sole responsibility is to move tasks from the 'scheduled' queue to
          a regular queue (e.g., 'default') once they're ready to be executed.
    """
    if not queues:
        raise MissingQueueError()

    broker_url = (
        broker_url
        or os.environ.get("AMQP_URL")
        or os.environ.get("CLOUDAMQP_URL")
        or os.environ.get("REDIS_URL")
        or os.environ.get("REDIS_TLS_URL")
    )

    if not broker_url:
        if AMQP_AVAILABLE:
            broker_url = "amqp://guest:guest@localhost:5672/"
        else:
            broker_url = "redis://localhost:6379/0"

    app = Celery(broker=broker_url)

    try:
        with app.connection_or_acquire() as connection:
            with connection.channel() as channel:
                worker_task_count = _job_queue_size_worker(app, queues)
                broker_task_count = _job_queue_size_broker(channel, queues)
                return worker_task_count + broker_task_count

    except OperationalError:
        return 0


def _job_queue_size_worker(app, queues):
    i = app.control.inspect()
    queues = set(queues)

    active_tasks = 0
    active = i.active()
    if active is not None:
        for worker, tasks in active.items():
            for task in tasks:
                if task["delivery_info"]["routing_key"] in queues:
                    active_tasks += 1

    reserved_tasks = 0
    reserved = i.reserved()
    if reserved is not None:
        for worker, tasks in reserved.items():
            for task in tasks:
                if task["delivery_info"]["routing_key"] in queues:
                    reserved_tasks += 1

    return active_tasks + reserved_tasks


def _job_queue_size_broker(channel, queues):
    if hasattr(channel, "_size"):
        fn = _job_queue_size_redis
    else:
        fn = _job_queue_size_rabbitmq

    return sum(fn(channel, queue) for queue in queues)


def _job_queue_size_redis(channel, queue):
    return channel.client.llen(queue)


def _job_queue_size_rabbitmq(channel, queue):
    try:
        return channel.queue_declare(queue=queue, passive=True).message_count
    except ChannelError:
        return 0


def job_queue_latency(*queues, broker_url=None):
    """
    Calculates the maximum latency across the specified queues using Celery with either
    Redis or RabbitMQ (AMQP) as the broker.

    This function dynamically selects the broker based on the provided broker_url, environment variables,
    or falls back to a default local broker URL. If RabbitMQ (AMQP) is available, it is preferred;
    otherwise, Redis is used.

    Args:
        queues (str): A variable number of queue names as strings.
        broker_url (str, optional): The broker URL. Defaults in order:
                                    - Passed argument broker_url.
                                    - Environment variables AMQP_URL, CLOUDAMQP_URL, REDIS_URL, REDIS_TLS_URL.
                                    - "amqp://guest:guest@localhost:5672/" if AMQP is available, otherwise
                                      "redis://localhost:6379/0".

    Returns:
        int: The maximum latency across the specified queues.

    Raises:
        MissingQueueError: If no queue names are provided.
    """
    if not queues:
        raise MissingQueueError()

    broker_url = (
        broker_url
        or os.environ.get("AMQP_URL")
        or os.environ.get("CLOUDAMQP_URL")
        or os.environ.get("REDIS_URL")
        or os.environ.get("REDIS_TLS_URL")
    )

    if not broker_url:
        if AMQP_AVAILABLE:
            broker_url = "amqp://guest:guest@localhost:5672/"
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


def _job_queue_latency_redis(channel, queue):
    oldest_job = channel.client.lindex(queue, -1)

    if oldest_job:
        oldest_job = json.loads(oldest_job.decode("utf-8"))
        run_at = oldest_job.get("headers", {}).get("run_at")

        if run_at:
            latency = time.time() - run_at
            return max(0, latency)

    return 0


def _job_queue_latency_rabbitmq(channel, queue):
    try:
        message = channel.basic_get(queue)

        if message is None:
            return 0

        run_at = message.headers.get("run_at")

        if run_at:
            latency = time.time() - run_at
            result = max(0, latency)
        else:
            result = 0

        channel.basic_reject(message.delivery_tag, requeue=True)

        return result
    except ChannelError:
        return 0


@before_task_publish.connect
def run_at_header_signal(
    sender=None, headers=None, body=None, properties=None, **kwargs
):
    headers = headers or {}

    if "run_at" not in headers:
        eta = headers.get("eta")
        countdown = headers.get("countdown")

        if eta:
            if isinstance(eta, str):
                eta = datetime.fromisoformat(eta)

            run_at_time = eta.timestamp()
        elif countdown is not None:
            run_at_time = time.time() + countdown
        else:
            run_at_time = time.time()

        headers["run_at"] = run_at_time
