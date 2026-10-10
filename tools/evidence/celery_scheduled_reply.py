import json
import os
import resource
import subprocess
import sys
import time

import redis
from celery import Celery

from hirefire_resource.macro import celery as macro

BROKER = os.environ["TOOLS_REDIS_URL"]
QUEUE = "evidence_scheduled"
PASSES = 6

app = Celery("celery_scheduled_reply", broker=BROKER)
app.conf.task_default_queue = QUEUE
app.conf.broker_connection_retry_on_startup = True


@app.task(name="evidence.later")
def later(payload):
    return None


def start_worker():
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "celery_scheduled_reply",
            "worker",
            "--pool=solo",
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


def publish(count):
    payload = "x" * 100
    with app.producer_or_acquire() as producer:
        for _ in range(count):
            later.apply_async((payload,), countdown=3600, producer=producer)


def wait_until_held(client, count, seconds=900):
    ends = time.monotonic() + seconds
    while time.monotonic() < ends:
        if client.llen(QUEUE) == 0 and client.hlen("unacked") >= count:
            return True
        time.sleep(1)
    return False


def megabytes(size):
    return round(size / 1_000_000, 1)


def peak_memory():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return megabytes(peak if sys.platform == "darwin" else peak * 1024)


def worker_memory(worker):
    rss = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(worker.pid)], capture_output=True, text=True
    ).stdout.strip()
    return megabytes(int(rss) * 1024) if rss else None


def whole_reply():
    started = time.monotonic()
    reply = app.control.inspect(timeout=120, limit=1).scheduled() or {}
    return {
        "seconds": round(time.monotonic() - started, 2),
        "megabytes": megabytes(len(json.dumps(reply))),
        "tasks": sum(len(tasks) for tasks in reply.values()),
    }


def unclaimed(mailbox):
    replies = [reply for ticket in mailbox.unclaimed.values() for reply in ticket]
    return {
        "replies": len(replies),
        "megabytes": megabytes(sum(len(json.dumps(reply)) for reply in replies)),
    }


def passes():
    owned = macro._owned_celery_app(BROKER)
    owned.control.mailbox.producer_pool = None
    rows = []
    for _ in range(PASSES):
        started = time.monotonic()
        try:
            outcome = macro._inspect_held_tasks(owned, BROKER)
        except Exception as error:
            outcome = f"raised {type(error).__name__}: {error}"
        elapsed = time.monotonic() - started
        rows.append(
            {
                "seconds": round(elapsed, 2),
                "counts": outcome,
                "replies_kept_for_a_question_that_is_over": unclaimed(
                    owned.control.mailbox
                ),
                "peak_memory_of_this_process_megabytes": peak_memory(),
            }
        )
        time.sleep(max(0.0, macro._HELD_TASKS_REFRESH_INTERVAL - elapsed))
    return rows


def main():
    count = int(sys.argv[1])
    client = redis.Redis.from_url(BROKER)
    client.flushdb()
    worker = start_worker()
    try:
        started = time.monotonic()
        publish(count)
        held = wait_until_held(client, count)
        result = {
            "scheduled_tasks": count,
            "worker_holds_them": held,
            "seconds_to_publish_and_hand_over": round(time.monotonic() - started),
            "worker_memory_megabytes": worker_memory(worker),
            "peak_memory_of_this_process_before_megabytes": peak_memory(),
            "passes_of_the_client": passes(),
            "whole_reply_with_a_timeout_of_120_seconds": whole_reply(),
            "worker_memory_after_megabytes": worker_memory(worker),
        }
    finally:
        worker.terminate()
        try:
            worker.wait(30)
        except subprocess.TimeoutExpired:
            worker.kill()
        client.flushdb()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
