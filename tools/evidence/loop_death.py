import json
import sys
import threading
import time

from evidence_support import boot
from fake_server import GRANT


def main():
    role = sys.argv[1]
    calls = {"count": 0}

    def sampler():
        calls["count"] += 1
        if calls["count"] == 2:
            raise SystemExit("sampler called sys.exit")
        return 1

    env = {"DYNO": "web.1"} if role == "web" else {"DYNO": "worker.1"}
    hirefire, server, lines = boot(
        lambda request: GRANT if request.path == "/metrics/lease" else {"status": 200},
        env=env,
        configure=lambda config: config.dyno("worker", sampler),
    )
    time.sleep(6)
    dispatcher = hirefire.configuration.dispatcher
    result = {
        "process": env["DYNO"],
        "sampler_calls_in_6s": calls["count"],
        "running_reports": dispatcher.running(),
        "job_queue_thread_alive": bool(
            dispatcher._job_queue_thread and dispatcher._job_queue_thread.is_alive()
        ),
        "threads": threading.active_count(),
        "log_lines_about_it": [line for line in lines if "exit" in line.lower()],
    }
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
