import json
import os
import sys
import time

from evidence_support import boot
from fake_server import GRANT, GRANT_HEADERS


def truncation():
    plan = json.dumps(
        {"job_queues": [{"name": f"queue-{i}", "strategy": "jqs"} for i in range(300)]}
    )
    grant = {"status": 200, "headers": GRANT_HEADERS, "body": plan}
    hirefire, server, lines = boot(
        lambda request: grant if request.path == "/metrics/lease" else {"status": 200},
        configure=lambda config: config.dyno("queue-0", lambda: 1),
    )
    time.sleep(2)
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    return {
        "valid_entries_sent": 300,
        "log_line": [line for line in lines if "truncated" in line][:1],
    }


def slow_sampler():
    def sampler():
        time.sleep(7)
        return 1

    hirefire, server, lines = boot(
        lambda request: GRANT if request.path == "/metrics/lease" else {"status": 200},
        configure=lambda config: config.dyno("worker", sampler),
    )
    time.sleep(31)
    times = [
        request.at for request in server.requests if request.path == "/metrics/lease"
    ]
    gaps = [round(b - a, 1) for a, b in zip(times, times[1:])]
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    return {
        "sampler_seconds": 7,
        "ttl_seconds": 5,
        "lease_requests_in_31s": len(times),
        "seconds_between_lease_requests": gaps,
    }


def one_off():
    hirefire, server, lines = boot(env={"DYNO": "run.4821"})
    time.sleep(3.5)
    running = hirefire.configuration.dispatcher.running()
    bodies = [
        request.body for request in server.requests if request.path == "/metrics/ingest"
    ]
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    return {
        "dyno": "run.4821",
        "dispatcher_running": running,
        "ingest_requests_in_3s": len(bodies),
        "example": bodies[-1][:80] if bodies else None,
    }


def fork_during_stall():
    hirefire, server, lines = boot(
        lambda request: {"then": "stall"}, env={"DYNO": "web.1"}
    )
    time.sleep(1.2)
    started = time.monotonic()
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    seconds = round(time.monotonic() - started, 2)
    os.waitpid(pid, 0)
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    return {
        "server": "stall",
        "seconds_os_fork_took_in_the_parent": seconds,
        "log": [line for line in lines if "did not stop" in line][:1],
    }


def masked_import():
    import rq  # noqa: F401

    sys.modules["hirefire_resource.macro.rq"] = None
    plan = json.dumps(
        {
            "job_queues": [
                {
                    "name": "worker",
                    "strategy": "jqs",
                    "adapter": "rq",
                    "queues": ["default"],
                }
            ]
        }
    )
    grant = {"status": 200, "headers": GRANT_HEADERS, "body": plan}
    hirefire, server, lines = boot(
        lambda request: grant if request.path == "/metrics/lease" else {"status": 200},
        configure=lambda config: config.dyno("other", lambda: 1),
    )
    time.sleep(2.5)
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    return {
        "macro_import": "fails with ImportError",
        "log": sorted({line for line in lines if "ERROR" in line or "WARN" in line}),
    }


def middleware_errors():
    hirefire, server, lines = boot(env={"DYNO": "web.1"})
    from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware

    def broken():
        raise RuntimeError("identity lookup failed")

    hirefire.configuration.http_source = broken
    app = HireFireMiddleware(lambda environ, start_response: [])
    for _ in range(100):
        app({"HTTP_X_REQUEST_START": f"t={time.time() - 0.01}"}, None)
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    errors = [line for line in lines if "Middleware error" in line]
    return {
        "failing_requests": 100,
        "error_lines": len(errors),
        "example": errors[0] if errors else None,
    }


if __name__ == "__main__":
    print(json.dumps({sys.argv[1]: globals()[sys.argv[1]]()}))
