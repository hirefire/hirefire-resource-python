import json
import sys
import time

from evidence_support import boot
from fake_server import GRANT


def main():
    hirefire, server, lines = boot(
        lambda request: GRANT if request.path == "/metrics/lease" else {"status": 200},
        env={"DYNO": "worker.1"},
    )
    before = "rq" in sys.modules
    import rq  # noqa: F401

    time.sleep(4)
    dispatcher = hirefire.configuration.dispatcher
    result = {
        "rq_loaded_at_boot": before,
        "rq_loaded_now": "rq" in sys.modules,
        "lease_requests_in_4s": server.request_counts.get("/metrics/lease", 0),
        "ingest_requests_in_4s": server.request_counts.get("/metrics/ingest", 0),
        "job_queue_thread": dispatcher._job_queue_thread is not None,
    }
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
