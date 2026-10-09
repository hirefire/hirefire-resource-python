import json
import time

from evidence_support import boot
from fake_server import GRANT, GRANT_HEADERS

NESTED = "[" * 60000 + "]" * 60000


def main():
    state = {"nested": False}

    def handler(request):
        if request.path != "/metrics/lease":
            return {"status": 200}
        if state["nested"]:
            return {"status": 200, "headers": GRANT_HEADERS, "body": NESTED}
        return GRANT

    calls = {"count": 0}

    def sampler():
        calls["count"] += 1
        return 1

    hirefire, server, lines = boot(
        handler, configure=lambda c: c.dyno("worker", sampler)
    )
    time.sleep(2.5)
    lease = hirefire.configuration.dispatcher._lease
    granted_before = lease.granted()
    state["nested"] = True
    calls_before = calls["count"]
    time.sleep(12)
    result = {
        "body_bytes": len(NESTED),
        "granted_before_nested_body": granted_before,
        "granted_after_two_nested_bodies": lease.granted(),
        "sampler_calls_while_server_sends_nested_body": calls["count"] - calls_before,
        "error_lines": sorted(
            {line[:120] for line in lines if line.startswith("ERROR")}
        ),
    }
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
