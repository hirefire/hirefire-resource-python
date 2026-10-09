import decimal
import fractions
import json
import time

from evidence_support import boot, wait_for
from fake_server import GRANT

VALUES = {
    "int": 7,
    "float": 7.5,
    "bool": True,
    "decimal": decimal.Decimal("7.5"),
    "fraction": fractions.Fraction(15, 2),
    "huge_int": 10**400,
    "nan": float("nan"),
    "negative": -1,
    "numeric_string": "7",
}


def main():
    name = __import__("sys").argv[1]
    value = VALUES[name]
    plan = json.dumps({"job_queues": [{"name": "worker", "strategy": "jqs"}]})
    grant = {**GRANT, "body": plan}

    def handler(request):
        return grant if request.path == "/metrics/lease" else {"status": 200}

    hirefire, server, lines = boot(
        handler, configure=lambda config: config.dyno("worker", lambda: value)
    )
    wait_for(
        lambda: any("jqs" in r.body for r in server.requests)
        or any(line.startswith("ERROR") for line in lines),
        4,
    )
    time.sleep(0.3)
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    sent = [r.body for r in server.requests if "jqs" in r.body]
    errors = [line for line in lines if line.startswith("ERROR")]
    print(
        json.dumps(
            {
                "sampler_returns": name,
                "sent": sent[0] if sent else None,
                "error_line": errors[0][:170] if errors else None,
            }
        )
    )


if __name__ == "__main__":
    main()
