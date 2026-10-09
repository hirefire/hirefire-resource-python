import json
import os
import sys

URL = os.environ["TOOLS_AMQP_URL"]
PEEKS = 25


def celery_run(queue_type):
    from celery import Celery
    from kombu import Queue

    import hirefire_resource.macro.celery as macro

    name = f"evidence_celery_{queue_type}"
    app = Celery(broker=URL)
    app.conf.task_queues = [Queue(name, queue_arguments={"x-queue-type": queue_type})]
    app.conf.broker_transport_options = {"confirm_publish": True}
    app.conf.broker_pool_limit = None
    app.send_task("evidence.task", queue=name)

    counts = []
    redelivered = None
    with app.connection_for_read() as connection:
        channel = connection.default_channel
        for _ in range(PEEKS):
            macro.job_queue_latency(name, broker_url=URL)
            counts.append(channel.queue_declare(queue=name, passive=True).message_count)
        message = channel.basic_get(name)
        if message is not None:
            redelivered = message.delivery_info.get("redelivered")
            headers = message.headers or {}
            channel.basic_reject(message.delivery_tag, requeue=True)
        else:
            headers = {}
        channel.queue_delete(name)
    return {
        "library": "celery",
        "queue_type": queue_type,
        "message_count_after_each_peek": counts,
        "redelivered_flag_after_peeks": redelivered,
        "x_delivery_count": headers.get("x-delivery-count"),
    }


def dramatiq_run(queue_type):
    import pika

    import hirefire_resource.macro.dramatiq as macro

    name = f"evidence_dramatiq_{queue_type}"
    connection = pika.BlockingConnection(pika.URLParameters(URL))
    channel = connection.channel()
    channel.queue_declare(name, durable=True, arguments={"x-queue-type": queue_type})
    body = json.dumps(
        {
            "queue_name": name,
            "actor_name": "work",
            "args": [],
            "kwargs": {},
            "options": {},
            "message_id": "evidence",
            "message_timestamp": 1,
        }
    )
    channel.confirm_delivery()
    channel.basic_publish("", name, body.encode())
    counts = []
    for _ in range(PEEKS):
        macro.job_queue_latency(name, broker_url=URL)
        counts.append(channel.queue_declare(name, passive=True).method.message_count)
    channel.queue_delete(name)
    connection.close()
    return {
        "library": "dramatiq",
        "queue_type": queue_type,
        "message_count_after_each_peek": counts,
    }


if __name__ == "__main__":
    run = celery_run if "celery" in sys.argv[1:] else dramatiq_run
    for queue_type in ("classic", "quorum"):
        print(json.dumps(run(queue_type)))
