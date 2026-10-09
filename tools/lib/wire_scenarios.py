import json
import logging
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_server import FakeServer  # noqa: E402

EPOCH = 1_767_225_600
UUID = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
IDENTITY_ENV = [
    "DYNO",
    "HIREFIRE_SERVICE_NAME",
    "RENDER_SERVICE_NAME",
    "RENDER_SERVICE_TYPE",
    "RENDER",
    "RENDER_CPU_COUNT",
    "HIREFIRE_VERBOSE",
]
ENVS = {
    "ingest_rqt": "py314-core",
    "ingest_rqt_idle": "py314-core",
    "ingest_cpu": "py314-core",
    "lease_local_sampler": "py314-core",
    "lease_plan_trace": "py314-rq2",
}
GRANT_HEADERS = {
    "HireFire-Lease-Granted": "true",
    "HireFire-Lease-TTL": "5",
    "HireFire-Sample-Frequency": "15",
}
TYPE_NAMES = {float: "Float", int: "Integer"}


def placeholder(value):
    return f"<{TYPE_NAMES.get(type(value), type(value).__name__)}>"


def scenario_ingest_rqt():
    def prepare(hirefire):
        for value in (25, 75, 200):
            hirefire.configuration.buffer.sample("web", "rqt", value)

    return capture(
        env={"DYNO": "web.1"},
        prepare=prepare,
        pick={"ingest": lambda body: "rqt" in body},
    )


def scenario_ingest_rqt_idle():
    return capture(env={"DYNO": "web.1"}, pick={"ingest": lambda body: "rqt" in body})


def scenario_ingest_cpu():
    def normalize(payload):
        for entry in payload:
            cpu = entry["metrics"].get("cpu")
            if cpu:
                for key in cpu:
                    cpu[key] = placeholder(cpu[key])

    return capture(
        env={"HIREFIRE_SERVICE_NAME": "worker"},
        pick={"ingest": lambda body: "cpu" in body},
        normalize=normalize,
    )


def scenario_lease_local_sampler():
    grant = {
        "status": 200,
        "headers": GRANT_HEADERS,
        "body": json.dumps({"job_queues": [{"name": "worker", "strategy": "jqs"}]}),
    }
    return capture(
        env={},
        lease=grant,
        configure=lambda config: config.dyno("worker", lambda: 42),
        pick={"lease": True, "ingest": lambda body: "jqs" in body},
    )


def scenario_lease_plan_trace():
    import redis
    import rq

    url = os.environ["TOOLS_REDIS_URL"]
    connection = redis.Redis.from_url(url)
    connection.flushdb()
    queue = rq.Queue("default", connection=connection)
    for _ in range(3):
        queue.enqueue("builtins.print")
    connection.close()

    plan = {
        "trace": True,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jqs",
                "adapter": "rq",
                "queues": ["default"],
                "options": {"skip_working": True},
            },
            {
                "name": "latency",
                "strategy": "jql",
                "adapter": "rq",
                "queues": ["empty"],
            },
        ],
    }

    def normalize(payload):
        for entry in payload:
            trace = entry.get("sample_trace")
            if not trace:
                continue
            trace["wave_ms"] = placeholder(trace["wave_ms"])
            for op in trace["ops"]:
                op["ms"] = placeholder(op["ms"])

    def whole_wave(body):
        payload = json.loads(body)
        names = [entry["name"] for entry in payload]
        return names == ["worker", "latency"] and "sample_trace" in payload[0]

    return capture(
        env={"HIREFIRE_RQ_URL": url},
        lease={
            "status": 200,
            "headers": {**GRANT_HEADERS, "HireFire-Sample-Frequency": "1"},
            "body": json.dumps(plan),
        },
        pick={"lease": True, "ingest": whole_wave},
        normalize=normalize,
    )


def capture(env, pick, lease=None, prepare=None, configure=None, normalize=None):
    def handler(request):
        if request.path == "/metrics/lease" and lease:
            return lease
        return {"status": 200}

    server = FakeServer(handler)
    for key in IDENTITY_ENV:
        os.environ.pop(key, None)
    os.environ.update(env)
    os.environ["HIREFIRE_TOKEN"] = "golden-token"
    os.environ["HIREFIRE_DATA_URL"] = server.url

    time.time = lambda: float(EPOCH)
    from hirefire_resource import HireFire

    logger = logging.getLogger("tools.wire")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False

    chosen = None
    if prepare:
        prepare(HireFire)
    with HireFire.configure() as config:
        config.logger = logger
        if configure:
            configure(config)
    deadline = time.monotonic() + 15
    while True:
        chosen = choose(server.requests, pick)
        if chosen or time.monotonic() > deadline:
            break
        time.sleep(0.02)
    HireFire.configuration.stop_dispatcher(flush=False)
    server.stop()
    if not chosen:
        seen = {}
        for request in server.requests:
            seen[request.path] = seen.get(request.path, 0) + 1
        sys.exit(f"scenario did not produce the expected requests (saw {seen})")
    return [record(request, normalize) for request in chosen]


def choose(requests, pick):
    found = []
    if pick.get("lease"):
        lease = next((r for r in requests if r.path == "/metrics/lease"), None)
        if lease is None:
            return None
        found.append(lease)
    if pick.get("ingest"):
        ingest = next(
            (
                r
                for r in requests
                if r.path == "/metrics/ingest" and pick["ingest"](r.body)
            ),
            None,
        )
        if ingest is None:
            return None
        found.append(ingest)
    return found


def record(request, normalize):
    body = request.body
    normalized = False
    if normalize and request.path == "/metrics/ingest":
        payload = json.loads(body)
        normalize(payload)
        body = json.dumps(payload, separators=(",", ":"))
        normalized = True
    return {
        "verb": request.verb,
        "path": request.path,
        "headers": [
            [name, header_value(name, value, normalized)]
            for name, value in request.headers
        ],
        "body": body,
        "body_normalized": normalized,
    }


def header_value(name, value, normalized):
    lowered = name.lower()
    if lowered == "host":
        return "<host>"
    if normalized and lowered == "content-length":
        return "<length>"
    if lowered == "hirefire-process-id" and UUID.match(value):
        return "<uuid>"
    return value


def run(name, path):
    requests = globals()[f"scenario_{name}"]()
    document = {"scenario": name, "epoch": EPOCH, "requests": requests}
    Path(path).write_text(json.dumps(document, indent=2) + "\n")


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
