import inspect
import json
import os
import threading

import redis
from celery import Celery, _state

from hirefire_resource.macro import celery as macro

BROKER = os.environ["TOOLS_REDIS_URL"]
PREFIX = "evidence-prefix:"
QUEUE = "evidence_host_app"


def app_names():
    return sorted(str(app.main) for app in _state._get_active_apps())


def from_a_sample_thread(host, result):
    result["current_app_of_the_thread"] = repr(_state._tls.current_app)
    result["default_app"] = repr(_state.default_app)
    result["registered_apps"] = app_names()
    result["size_by_url"] = macro.job_queue_size(
        QUEUE, broker_url=BROKER, skip_working=True
    )
    result["latency_by_url"] = macro.job_queue_latency(QUEUE, broker_url=BROKER)
    result["size_by_the_app_of_the_host"] = macro.job_queue_size(
        QUEUE, celery_app=host, skip_working=True
    )
    result["registered_apps_after_three_samples"] = app_names()

    from celery import current_app

    result["main_of_celery_current_app_read_in_the_thread"] = current_app.main
    result["default_app_after_that_read"] = _state.default_app.main
    result["registered_apps_after_that_read"] = app_names()


def main():
    client = redis.Redis.from_url(BROKER)
    client.flushdb()
    host = Celery("host", broker=BROKER)
    host.conf.broker_transport_options = {"global_keyprefix": PREFIX}
    host.send_task("evidence.task", queue=QUEUE)

    result = {
        "messages_under_the_prefixed_key": client.llen(PREFIX + QUEUE),
        "messages_under_the_plain_key": client.llen(QUEUE),
        "current_app_of_the_main_thread": _state._tls.current_app.main,
    }
    thread = threading.Thread(target=from_a_sample_thread, args=(host, result))
    thread.start()
    thread.join()

    second = Celery("second", broker=BROKER, set_as_current=False)
    result["registered_apps_with_a_second_app_of_the_host"] = app_names()
    result["parameters_of_job_queue_latency"] = list(
        inspect.signature(macro.job_queue_latency).parameters
    )
    del second
    client.flushdb()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
