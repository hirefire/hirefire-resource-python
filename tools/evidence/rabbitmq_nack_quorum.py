import json
import os

import pika

URL = os.environ["TOOLS_AMQP_URL"]
PEEKS = 40


def run(method):
    name = f"evidence_{method}_quorum"
    connection = pika.BlockingConnection(pika.URLParameters(URL))
    channel = connection.channel()
    channel.queue_declare(name, durable=True, arguments={"x-queue-type": "quorum"})
    channel.confirm_delivery()
    channel.basic_publish("", name, b"{}")
    for _ in range(PEEKS):
        frame, _properties, _body = channel.basic_get(name, auto_ack=False)
        if frame is not None:
            if method == "reject":
                channel.basic_reject(frame.delivery_tag, requeue=True)
            else:
                channel.basic_nack(frame.delivery_tag, requeue=True)
    still_there = channel.basic_get(name, auto_ack=True)[0] is not None
    channel.queue_delete(name)
    connection.close()
    return {
        "returned_with": f"basic_{method}(requeue=True)",
        "peeks": PEEKS,
        "message_still_in_the_queue": still_there,
    }


if __name__ == "__main__":
    for method in ("reject", "nack"):
        print(json.dumps(run(method)))
