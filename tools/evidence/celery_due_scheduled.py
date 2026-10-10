import json
import os
import subprocess
import sys
import time

import redis
from celery import Celery

BROKER = os.environ["TOOLS_REDIS_URL"]
QUEUE = "evidence_due"

app = Celery("celery_due_scheduled", broker=BROKER)
app.conf.task_default_queue = QUEUE
app.conf.broker_connection_retry_on_startup = True


@app.task(name="evidence.busy")
def busy(seconds):
    time.sleep(seconds)


@app.task(name="evidence.later")
def later():
    return None


def start_worker(pool):
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "celery_due_scheduled",
            "worker",
            f"--pool={pool}",
            "--concurrency=1",
            "-Q",
            QUEUE,
            "--loglevel=ERROR",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
        ],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def names(reply, scheduled=False):
    tasks = [task for worker in (reply or {}).values() for task in worker]
    return sorted((task["request"] if scheduled else task)["name"] for task in tasks)


def held(inspect):
    return {
        "active": names(inspect.active()),
        "reserved": names(inspect.reserved()),
        "scheduled": names(inspect.scheduled(), scheduled=True),
    }


def measure(pool):
    client = redis.Redis.from_url(BROKER)
    client.flushdb()
    worker = start_worker(pool)
    try:
        inspect = app.control.inspect(timeout=1)
        answered = any(inspect.ping() for _ in range(30))
        busy.delay(20)
        time.sleep(1)
        for _ in range(5):
            later.apply_async(countdown=8)
        time.sleep(1)
        before = held(inspect)
        time.sleep(6)
        after = held(inspect)
    finally:
        worker.terminate()
        try:
            worker.wait(30)
        except subprocess.TimeoutExpired:
            worker.kill()
        client.flushdb()
    return {
        "worker_answered_before_the_busy_task": answered,
        "before_the_time_of_the_five_tasks": before,
        "after_their_time_with_the_one_process_still_busy": after,
    }


if __name__ == "__main__":
    print(json.dumps({pool: measure(pool) for pool in sys.argv[1:]}, indent=2))
