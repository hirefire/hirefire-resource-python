import json
import os
import time

import redis
from celery import Celery

from hirefire_resource.macro.celery import job_queue_latency

BROKER = os.environ["TOOLS_REDIS_URL"]


def publish(protocol):
    queue = f"evidence_protocol_{protocol}"
    app = Celery(broker=BROKER)
    app.conf.task_protocol = protocol
    app.send_task("evidence.task", queue=queue)
    return queue


def main():
    client = redis.Redis.from_url(BROKER)
    client.flushdb()
    queues = {protocol: publish(protocol) for protocol in (1, 2)}
    time.sleep(3)
    result = {}
    for protocol, queue in queues.items():
        message = json.loads(client.lindex(queue, -1))
        result[f"task_protocol_{protocol}"] = {
            "header_names": sorted(message["headers"]),
            "run_at_header": message["headers"].get("run_at"),
            "latency_after_3s": round(job_queue_latency(queue, broker_url=BROKER), 2),
        }
    client.flushdb()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
