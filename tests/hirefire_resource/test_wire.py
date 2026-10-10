import json
import logging
import os
import re
import socket
import time
from decimal import Decimal
from fractions import Fraction

import pytest

from hirefire_resource import HireFire
from hirefire_resource.dispatcher.session import Session
from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware
from hirefire_resource.version import VERSION

EPOCH = 1_767_225_600
UUID = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
GRANT_HEADERS = {
    "HireFire-Lease-Granted": "true",
    "HireFire-Lease-TTL": "5",
    "HireFire-Sample-Frequency": "15",
}
WORKER_PLAN = {"job_queues": [{"name": "worker", "strategy": "jqs"}]}
NO_GRANT = {
    "status": 200,
    "headers": {"HireFire-Lease-Granted": "false", "HireFire-Lease-TTL": "5"},
}
INGEST = "/metrics/ingest"
LEASE = "/metrics/lease"


def grant(plan=WORKER_PLAN):
    return {"status": 200, "headers": GRANT_HEADERS, "body": json.dumps(plan)}


class Wire:
    def __init__(self, server):
        self.server = server
        self.ingest = {"status": 200}
        self.lease = NO_GRANT

    def handle(self, request):
        action = self.lease if request.path == LEASE else self.ingest
        return action(request) if callable(action) else action

    def wait_for(self, what, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = predicate()
            if found:
                return found
            time.sleep(0.01)
        pytest.fail(f"timed out waiting for {what}, saw {self.server.paths()}")

    def wait_for_request(self, path, containing="", after=0):
        return self.wait_for(
            f"a request to {path}",
            lambda: next(
                (
                    request
                    for request in self.server.requests[after:]
                    if request.path == path and containing in request.body
                ),
                None,
            ),
        )

    def serve_request(self, queued_for):
        environ = {"HTTP_X_REQUEST_START": f"t={time.time() - queued_for:.3f}"}
        app = HireFireMiddleware(lambda environ, start_response: [b"served"])
        assert app(environ, None) == [b"served"]


@pytest.fixture
def wire(monkeypatch, fake_server):
    wire = Wire(fake_server)
    fake_server.handler = wire.handle
    monkeypatch.setenv("HIREFIRE_TOKEN", "wire-token")
    monkeypatch.setenv("HIREFIRE_DATA_URL", fake_server.url)
    monkeypatch.setattr(Session, "PREFORK_WAIT", 0)
    yield wire
    HireFire.configuration.stop_dispatcher(flush=False)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: float(EPOCH))


def errors(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR
    ]


def test_the_first_request_of_a_web_process_carries_its_request_queue_time(
    wire, monkeypatch, frozen_clock
):
    monkeypatch.setenv("DYNO", "web.1")

    wire.serve_request(queued_for=0.025)
    request = wire.wait_for_request(INGEST)

    assert request.verb == "POST"
    assert request.header("Content-Type") == "application/json"
    assert request.header("HireFire-Token") == "wire-token"
    assert request.header("HireFire-Agent") == f"Python-{VERSION}"
    assert request.body == (
        f'[{{"name":"web","metrics":{{"rqt":{{"{EPOCH}":[25.0,1]}}}}}}]'
    )


def test_an_idle_web_process_reports_the_current_second_as_empty(
    wire, monkeypatch, frozen_clock
):
    monkeypatch.setenv("DYNO", "web.1")

    HireFire.boot()

    assert wire.wait_for_request(INGEST).body == (
        f'[{{"name":"web","metrics":{{"rqt":{{"{EPOCH}":[]}}}}}}]'
    )


def test_a_process_reports_its_cpu_use_under_its_name(wire, monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "clock")

    HireFire.boot()
    request = wire.wait_for_request(INGEST, containing="cpu")

    [entry] = json.loads(request.body)
    assert entry["name"] == "clock"
    [(second, percent)] = entry["metrics"]["cpu"].items()
    assert re.fullmatch(r"\d{10}", second)
    assert isinstance(percent, float)
    assert 0.0 <= percent <= 100.0
    assert LEASE not in wire.server.paths()


def test_a_process_with_a_sampler_asks_for_the_lease_and_reports_what_the_plan_names(
    wire,
):
    wire.lease = grant()

    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
    lease = wire.wait_for_request(LEASE)
    ingest = wire.wait_for_request(INGEST, containing="jqs")

    assert lease.verb == "POST"
    assert lease.body == ""
    assert lease.header("HireFire-Token") == "wire-token"
    assert lease.header("HireFire-Agent") == f"Python-{VERSION}"
    assert UUID.match(lease.header("HireFire-Process-ID"))
    assert re.fullmatch(
        r'\[\{"name":"worker","metrics":\{"jqs":\{"\d{10}":42\}\}\}\]', ingest.body
    )


@pytest.mark.parametrize("value", [Decimal("7.5"), Fraction(15, 2)])
def test_a_sampler_that_returns_a_decimal_or_a_fraction_reports_it_as_a_number(
    wire, caplog, value
):
    wire.lease = grant()

    with HireFire.configure() as config:
        config.dyno("worker", lambda: value)
    ingest = wire.wait_for_request(INGEST, containing="jqs")

    assert re.fullmatch(
        r'\[\{"name":"worker","metrics":\{"jqs":\{"\d{10}":7\.5\}\}\}\]', ingest.body
    )
    assert errors(caplog) == []


def test_a_plan_that_asks_for_a_trace_gets_one_with_the_sample(wire):
    wire.lease = grant({**WORKER_PLAN, "trace": True})

    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
    request = wire.wait_for_request(INGEST, containing="sample_trace")

    [entry] = json.loads(request.body)
    assert entry["name"] == "worker"
    assert list(entry["metrics"]) == ["jqs"]
    trace = entry["sample_trace"]
    assert isinstance(trace["wave_ms"], float)
    assert [op["strategy"] for op in trace["ops"]] == ["jqs"]


def test_a_process_that_is_not_granted_the_lease_reports_no_job_metrics(
    wire, frozen_clock
):
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
    wire.wait_for_request(LEASE)
    HireFire.configuration.stop_dispatcher()

    assert wire.server.paths() == [LEASE]


def test_a_process_without_a_sampler_or_a_job_library_does_not_ask_for_the_lease(
    wire, monkeypatch, frozen_clock
):
    monkeypatch.setenv("DYNO", "web.1")

    HireFire.boot()
    wire.wait_for_request(INGEST)
    HireFire.configuration.stop_dispatcher()

    assert set(wire.server.paths()) == {INGEST}


def test_a_process_without_a_token_opens_no_connection(wire, monkeypatch):
    monkeypatch.delenv("HIREFIRE_TOKEN")
    monkeypatch.setenv("DYNO", "web.1")

    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
    wire.serve_request(queued_for=0.025)
    time.sleep(0.3)

    assert not HireFire.configuration.dispatcher.running()
    assert wire.server.accepted == 0


def test_stop_sends_what_is_still_buffered(wire, monkeypatch, frozen_clock):
    monkeypatch.setenv("DYNO", "web.1")
    HireFire.boot()
    wire.wait_for_request(INGEST)
    sent = len(wire.server.requests)

    wire.serve_request(queued_for=0.025)
    HireFire.configuration.stop_dispatcher()

    bodies = "".join(request.body for request in wire.server.requests[sent - 1 :])
    assert "[25.0,1]" in bodies


def test_a_stop_closes_the_connections_of_a_worker(wire):
    wire.lease = grant()
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
    wire.wait_for_request(LEASE)
    wire.wait_for_request(INGEST, containing="jqs")
    assert wire.server.open_sockets >= 2

    HireFire.configuration.stop_dispatcher(flush=False)

    wire.wait_for(
        "the server to see both connections closed",
        lambda: wire.server.open_sockets == 0,
    )


def test_the_final_flush_goes_out_on_the_connection_the_dispatch_loop_had(
    wire, monkeypatch, frozen_clock
):
    monkeypatch.setenv("DYNO", "web.1")
    wire.serve_request(queued_for=0.025)
    wire.wait_for_request(INGEST)
    connections = wire.server.accepted
    requests = len(wire.server.requests)
    wire.serve_request(queued_for=0.025)

    HireFire.configuration.stop_dispatcher()

    assert len(wire.server.requests) > requests
    assert wire.server.accepted == connections


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded:DeprecationWarning"
)
def test_the_child_of_a_forking_worker_opens_no_connection(wire, monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")
    wire.lease = grant()
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
    wire.wait_for_request(INGEST, containing="jqs")
    connections = wire.server.accepted

    pid = os.fork()
    if pid == 0:
        time.sleep(1.2)
        os._exit(0)
    _, status = os.waitpid(pid, 0)

    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    assert wire.server.accepted == connections


def test_a_failed_dispatch_is_logged_and_the_next_one_succeeds(
    wire, monkeypatch, caplog
):
    monkeypatch.setenv("DYNO", "web.1")
    answers = iter([{"status": 500}])
    wire.ingest = lambda request: next(answers, {"status": 200})

    HireFire.boot()
    wire.wait_for("a second dispatch", lambda: wire.server.paths().count(INGEST) >= 2)

    assert HireFire.configuration.dispatcher.running()
    assert [line for line in errors(caplog) if "Dispatch error" in line]
    assert len(errors(caplog)) == 1


def test_a_rejected_token_is_not_logged(wire, monkeypatch, caplog):
    monkeypatch.setenv("DYNO", "web.1")
    wire.ingest = {"status": 401}

    HireFire.boot()
    wire.wait_for("a second dispatch", lambda: wire.server.paths().count(INGEST) >= 2)

    assert HireFire.configuration.dispatcher.running()
    assert errors(caplog) == []


def test_a_refused_connection_is_logged_and_leaves_the_dispatcher_running(
    monkeypatch, allow_network, caplog
):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        address = probe.getsockname()
    allow_network(address)
    monkeypatch.setenv("DYNO", "web.1")
    monkeypatch.setenv("HIREFIRE_TOKEN", "wire-token")
    monkeypatch.setenv("HIREFIRE_DATA_URL", f"http://127.0.0.1:{address[1]}")

    HireFire.boot()
    deadline = time.monotonic() + 5
    while not errors(caplog) and time.monotonic() < deadline:
        time.sleep(0.01)

    assert [line for line in errors(caplog) if "Dispatch error" in line]
    assert HireFire.configuration.dispatcher.running()


def test_a_failed_lease_request_is_logged_and_leaves_the_dispatcher_running(
    wire, caplog
):
    wire.lease = {"status": 500}

    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
    wire.wait_for_request(LEASE)
    deadline = time.monotonic() + 5
    while not errors(caplog) and time.monotonic() < deadline:
        time.sleep(0.01)

    assert errors(caplog)
    assert HireFire.configuration.dispatcher.running()
