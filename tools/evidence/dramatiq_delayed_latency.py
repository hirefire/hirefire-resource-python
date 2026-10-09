import json
import os
import time

import dramatiq
import redis
from dramatiq.brokers.redis import RedisBroker

URL = os.environ["TOOLS_REDIS_URL"]
NAMESPACE = "evidence"
DELAY_MS = 5000

client = redis.Redis.from_url(URL)
client.flushdb()
broker = RedisBroker(url=URL, namespace=NAMESPACE)
dramatiq.set_broker(broker)


@dramatiq.actor(queue_name="ev", max_retries=0)
def block(seconds):
    time.sleep(seconds)


@dramatiq.actor(queue_name="ev", max_retries=0)
def work():
    pass


def main():
    from hirefire_resource.macro.dramatiq import job_queue_latency, job_queue_size

    def read():
        return {
            "latency": round(
                job_queue_latency("ev", broker_url=URL, namespace=NAMESPACE), 2
            ),
            "size": job_queue_size("ev", broker_url=URL, namespace=NAMESPACE),
        }

    block.send(12)
    block.send(12)
    started = time.time()
    work.send_with_options(delay=DELAY_MS)
    worker = dramatiq.Worker(broker, worker_threads=1)
    worker.start()
    samples = []
    try:
        for _ in range(8):
            time.sleep(1)
            samples.append({"second": round(time.time() - started, 1), **read()})
    finally:
        worker.stop(timeout=1000)
        client.flushdb()
    promoted = [s for s in samples if s["size"] > 0]
    result = {
        "delay_seconds": DELAY_MS / 1000,
        "samples": samples,
        "first_sample_after_promotion": promoted[0] if promoted else None,
        "seconds_ready_at_that_sample": round(
            promoted[0]["second"] - DELAY_MS / 1000, 1
        )
        if promoted
        else None,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
