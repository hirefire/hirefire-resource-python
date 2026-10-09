import json
import subprocess
import sys
import threading
import time

from fake_server import GRANT_HEADERS, FakeServer

PLAN = json.dumps({"job_queues": [{"name": "worker", "strategy": "jqs"}]})
SERVER_LEASE = 10.0
CLIENT = r"""
import logging, os, sys, time
import rq
from hirefire_resource import HireFire
logger = logging.getLogger("silent"); logger.addHandler(logging.NullHandler()); logger.propagate = False
with HireFire.configure() as config:
    config.logger = logger
    if sys.argv[1] == "capable":
        config.dyno("worker", lambda: 1)
time.sleep(float(sys.argv[2]))
"""


def trial(incapable, seconds):
    lock = threading.Lock()
    state = {"holder": None, "expires": 0.0}
    grants = []

    def handler(request):
        if request.path != "/metrics/lease":
            return {"status": 200}
        identifier = request.header("HireFire-Process-ID")
        now = time.monotonic()
        with lock:
            free = state["holder"] is None or now >= state["expires"]
            if free or state["holder"] == identifier:
                renewal = state["holder"] == identifier and not free
                state["holder"] = identifier
                state["expires"] = now + SERVER_LEASE
                grants.append((now, identifier, renewal))
                return {"status": 200, "headers": GRANT_HEADERS, "body": PLAN}
        return {
            "status": 200,
            "headers": {"HireFire-Lease-Granted": "false", "HireFire-Lease-TTL": "5"},
        }

    server = FakeServer(handler, record=False)
    environment = {
        **__import__("os").environ,
        "HIREFIRE_TOKEN": "evidence-token",
        "HIREFIRE_DATA_URL": server.url,
        "DYNO": "worker.1",
    }
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", CLIENT, "incapable", str(seconds + 3)],
            env=environment,
        )
        for _ in range(incapable)
    ]
    time.sleep(3)
    capable_started = time.monotonic()
    processes.append(
        subprocess.Popen(
            [sys.executable, "-c", CLIENT, "capable", str(seconds)], env=environment
        )
    )
    for process in processes:
        process.wait()
    server.stop()
    renewed = {identifier for _at, identifier, renewal in grants if renewal}
    first_held = next(
        (at for at, identifier, _ in grants if identifier in renewed), None
    )
    dropped = sum(
        1
        for at, identifier, _ in grants
        if identifier not in renewed and at >= capable_started
    )
    return {
        "processes_that_cannot_sample": incapable,
        "seconds_until_the_capable_process_held_the_lease": None
        if first_held is None
        else round(first_held - capable_started, 1),
        "grants_taken_and_dropped_meanwhile": dropped,
        "observed_seconds": seconds,
    }


if __name__ == "__main__":
    for incapable in (0, 1, 5):
        print(json.dumps(trial(incapable, 150)), flush=True)
