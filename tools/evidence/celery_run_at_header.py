import json
import os
import subprocess
import sys
import time

BROKER = os.environ["TOOLS_REDIS_URL"]

PUBLISHER = r"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.environ["PYTHONPATH"].split(os.pathsep)[1]))
from fake_server import FakeServer
server = FakeServer(lambda request: {"status": 200})
os.environ["HIREFIRE_TOKEN"] = "evidence-token"
os.environ["HIREFIRE_DATA_URL"] = server.url
os.environ["DYNO"] = "web.1"
from celery import Celery
app = Celery(broker=sys.argv[1])
if sys.argv[3] == "import":
    import hirefire_resource.macro.celery
import logging
from hirefire_resource import HireFire
with HireFire.configure() as config:
    config.logger = logging.getLogger("silent")
    config.logger.addHandler(logging.NullHandler())
    config.logger.propagate = False
deadline = time.monotonic() + 5
while time.monotonic() < deadline and server.request_counts.get("/metrics/lease", 0) < 1:
    time.sleep(0.05)
app.send_task("evidence.task", queue=sys.argv[2])
print(json.dumps({
    "lease_requests": server.request_counts.get("/metrics/lease", 0),
    "macro_imported": "hirefire_resource.macro.celery" in sys.modules,
}))
HireFire.configuration.stop_dispatcher(flush=False)
server.stop()
"""


def publish(queue, mode):
    output = subprocess.run(
        [sys.executable, "-c", PUBLISHER, BROKER, queue, mode],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(output.strip().splitlines()[-1])


def main():
    import redis

    client = redis.Redis.from_url(BROKER)
    client.flushdb()
    plain = publish("evidence_plain", "plain")
    imported = publish("evidence_imported", "import")
    time.sleep(3)

    from hirefire_resource.macro.celery import job_queue_latency, job_queue_size

    result = {
        "publisher_without_macro": {
            **plain,
            "run_at_header": json.loads(client.lindex("evidence_plain", -1))[
                "headers"
            ].get("run_at"),
            "size": job_queue_size(
                "evidence_plain", broker_url=BROKER, skip_working=True
            ),
            "latency_after_3s": round(
                job_queue_latency("evidence_plain", broker_url=BROKER), 2
            ),
        },
        "publisher_with_macro": {
            **imported,
            "run_at_header": json.loads(client.lindex("evidence_imported", -1))[
                "headers"
            ].get("run_at"),
            "size": job_queue_size(
                "evidence_imported", broker_url=BROKER, skip_working=True
            ),
            "latency_after_3s": round(
                job_queue_latency("evidence_imported", broker_url=BROKER), 2
            ),
        },
    }
    client.flushdb()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
