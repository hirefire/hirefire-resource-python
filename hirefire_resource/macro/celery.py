import os

from celery import Celery
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
        >>> job_queue_size('default')
        42
        >>> job_queue_size('default', 'high_priority')
        85
        >>> job_queue_size('default', broker_url='amqp://user:password@host:5672/vhost')
        30

    Note:
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
                if hasattr(channel, "_size"):
                    fn = _job_queue_size_redis
                else:
                    fn = _job_queue_size_rabbitmq
                return sum(fn(channel, queue) for queue in queues)

    except OperationalError:
        return 0


def _job_queue_size_redis(channel, queue):
    return channel.client.llen(queue)


def _job_queue_size_rabbitmq(channel, queue):
    try:
        return channel.queue_declare(queue=queue, passive=True).message_count
    except ChannelError:
        return 0
