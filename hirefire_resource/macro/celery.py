from celery import Celery
from kombu.exceptions import OperationalError

try:
    from amqp.exceptions import ChannelError
except ImportError:
    class ChannelError(Exception):
        pass

def job_queue_size(*queues, broker_url):
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
