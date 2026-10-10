import json
import logging
import os
import queue
import re
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from mocket import Mocket, mocketize
from mocket.mockhttp import Entry, Request, Response

from hirefire_resource import HireFire, identity, plan
from hirefire_resource.client import Client
from hirefire_resource.client import Response as ClientResponse
from hirefire_resource.dispatcher import Dispatcher, payload
from hirefire_resource.dispatcher.session import Session
from hirefire_resource.errors import RequestError
from hirefire_resource.lease import Lease
from hirefire_resource.once import Once
from hirefire_resource.sampler import Sampler
from hirefire_resource.source.cpu import CPU
from hirefire_resource.source.cpu.usage import Usage
from tests.helpers import at, fake_macro, set_HIREFIRE_TOKEN  # noqa: F401

INGEST_URL = "https://data.hirefire.io/metrics/ingest"
LEASE_URL = "https://data.hirefire.io/metrics/lease"


@pytest.fixture(autouse=True)
def with_token(set_HIREFIRE_TOKEN):
    pass


@pytest.fixture(autouse=True)
def without_prefork_wait(monkeypatch):
    monkeypatch.setattr(Session, "PREFORK_WAIT", 0)


class SessionOnFirstUse:
    def __getattr__(self, name):
        made = self.__dict__.get("made")
        if made is None:
            made = self.__dict__["made"] = Session(HireFire.configuration)
        return getattr(made, name)


@pytest.fixture
def session(with_token):
    return SessionOnFirstUse()


def job_queue_pass(session):
    session.renew()
    session.sample()


def stub_lease(granted=False, plan=None, trace=False):
    headers = {
        "HireFire-Lease-Granted": str(granted).lower(),
        "HireFire-Sample-Frequency": "15",
    }
    body = ""
    if granted:
        if plan is None:
            plan = {
                "version": 1,
                "job_queues": [
                    {"name": "worker", "strategy": "jql"},
                    {"name": "mailer", "strategy": "jql"},
                ],
            }
        if trace:
            plan = dict(plan)
            plan["trace"] = True
        body = json.dumps(plan)
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers=headers,
        body=body,
    )


def complete_body(request):
    if "body" not in vars(request):
        expected = int(request.headers.get("content-length", 0))
        received, _closed = request._parser.trailing_data
        if expected == 0 or len(received) < expected:
            return None
    return request.body


class IngestBodies:
    def _items(self):
        return [
            json.loads(body)
            for request in list(Mocket.request_list())
            if request.path == "/metrics/ingest"
            and (body := complete_body(request)) is not None
        ]

    def __len__(self):
        return len(self._items())

    def __getitem__(self, index):
        return self._items()[index]

    def __iter__(self):
        return iter(self._items())

    def __eq__(self, other):
        return self._items() == other


def capture_ingest_bodies(status=200):
    Entry.single_register(Entry.POST, INGEST_URL, status=status)
    return IngestBodies()


INGEST_HEAD = (
    b"POST /metrics/ingest HTTP/1.1\r\n"
    b"Host: data.hirefire.io\r\n"
    b"Content-Length: 2\r\n\r\n"
)


@mocketize
def test_ingest_bodies_skip_a_request_whose_body_has_not_arrived():
    Mocket.collect(Request(INGEST_HEAD))

    assert list(IngestBodies()) == []


@mocketize
def test_ingest_bodies_read_a_request_whose_body_arrived():
    Mocket.collect(Request(INGEST_HEAD + b"[]"))
    bodies = IngestBodies()

    assert list(bodies) == [[]]
    assert list(bodies) == [[]]


def answer(status=200, headers=None):
    return ClientResponse(status=status, headers=headers or {})


@contextmanager
def ingest(submit):
    with patch.object(
        Client,
        "submit_samples",
        autospec=True,
        side_effect=lambda _, body: submit(body),
    ):
        yield


def ingest_statuses(*statuses):
    Entry.register(
        Entry.POST, INGEST_URL, *[Response(status=status) for status in statuses]
    )
    return IngestBodies()


def ingest_texts():
    return [
        body
        for request in list(Mocket.request_list())
        if request.path == "/metrics/ingest"
        and (body := complete_body(request)) is not None
    ]


def ingest_requests():
    return request_paths().count("/metrics/ingest")


def lease_requests():
    return request_paths().count("/metrics/lease")


def messages(caplog):
    return [record.getMessage() for record in caplog.records]


def sent(bodies, name, strategy, leaf):
    return any(
        leaf in entry["metrics"].get(strategy, {}).values()
        for body in bodies
        for entry in body
        if entry["name"] == name
    )


def seconds_claimed_at(session, second, bodies):
    with freeze_time(at(second)):
        session.report()
    return sorted(bodies[-1][0]["metrics"]["rqt"])


def attempts(session, seconds):
    seen = []
    with freeze_time(at(seconds[0])) as clock:
        for second in seconds:
            clock.move_to(at(second))
            before = ingest_requests()
            session.report()
            if ingest_requests() > before:
                seen.append(second)
    return seen


def renew_each_second(session, seconds):
    with freeze_time(at(seconds[0])) as clock:
        for second in seconds:
            clock.move_to(at(second))
            session.renew()


def loop_threads(name):
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == name and thread.is_alive()
    ]


def wait_until(condition, seconds=2):
    for _ in range(int(seconds / 0.005)):
        if condition():
            return
        time.sleep(0.005)
    pytest.fail(f"the condition was not met within {seconds} seconds")


def seconds_to(call):
    started = time.perf_counter()
    call()
    return time.perf_counter() - started


def configure_web_and_workers(monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("DYNO", "web.1")
    else:
        os.environ["DYNO"] = "web.1"
    config = HireFire.configuration
    config.dyno("worker", lambda: 42)
    config.dyno("mailer", lambda: 18)
    return config.dispatcher


def inject_oversized_series(name="web", strategy="rqt"):
    buffer = HireFire.configuration.buffer
    now = int(time.time())
    with buffer._mutex:
        for i in range(400):
            process_name = f"p{i}-{'x' * 48}"
            series = {}
            for s in range(60):
                series[now - s] = {"sum": 1.0, "count": 1} if strategy == "rqt" else 1.0
            buffer._metrics[process_name] = {strategy: series}
        buffer._metrics.setdefault(name, {})[strategy] = {
            now: {"sum": 1.0, "count": 1} if strategy == "rqt" else 1.0
        }


def configure_web_only(monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("DYNO", "web.1")
    else:
        os.environ["DYNO"] = "web.1"
    config = HireFire.configuration
    return config.dispatcher


def configure_workers_only():
    config = HireFire.configuration
    config.dyno("worker", lambda: 42)
    config.dyno("mailer", lambda: 18)
    return config.dispatcher


def configure_cpu_only(monkeypatch, name="clock"):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", name)
    return HireFire.configuration.dispatcher


def request_paths():
    return [request.path for request in Mocket.request_list()]


@mocketize
def test_starts_and_stops():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_and_workers()

    assert not dispatcher.running()
    assert dispatcher.start()
    assert dispatcher.running()
    assert not dispatcher.start()
    assert dispatcher.stop()
    assert not dispatcher.running()
    assert not dispatcher.stop()


@mocketize
def test_a_failed_thread_spawn_leaves_the_dispatcher_retryable(caplog):
    caplog.set_level(logging.ERROR)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()

    with patch("threading.Thread.start", side_effect=RuntimeError("cannot spawn")):
        assert not dispatcher.start()
        assert not dispatcher.running()
    assert "Could not start dispatcher" in caplog.text

    assert dispatcher.start()
    assert dispatcher.running()
    dispatcher.stop()


@mocketize
def test_a_start_without_package_metadata_warns_once_that_the_version_is_unknown(
    monkeypatch, caplog
):
    monkeypatch.setattr("hirefire_resource.version.VERSION", "unknown")
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()

    for _ in range(2):
        assert dispatcher.start()
        assert dispatcher.stop()

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == [
        "[HireFire] The version of hirefire-resource is unknown, because the "
        "metadata of the package (its dist-info directory) is not installed. "
        "HireFire treats a client without a version as a legacy client. "
        "Install the package with its metadata."
    ]


@mocketize
def test_a_start_with_package_metadata_does_not_warn(caplog):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()

    assert dispatcher.start()
    assert dispatcher.stop()

    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


@mocketize
def test_dispatches_web_metrics(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 12)
        HireFire.configuration.buffer.sample("web", "rqt", 8)
        session.report()

    assert len(bodies) == 1
    assert bodies[0][0]["name"] == "web"
    assert list(bodies[0][0]["metrics"]["rqt"].values())[0] == [10.0, 2]


@mocketize
def test_logs_the_payload_when_verbose_is_set(monkeypatch, caplog, session):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("HIREFIRE_VERBOSE", "1")
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 12)
        session.report()

    assert "Dispatching metrics" in caplog.text


@mocketize
def test_no_dispatch_when_nothing_configured(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    session.report()

    assert bodies == []


@mocketize
def test_first_dispatch_claims_only_the_current_second(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()

    assert bodies[0][0]["metrics"]["rqt"] == {"1000": []}


@mocketize
def test_backfills_seconds_skipped_between_dispatches(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1003)):
        session.report()

    assert bodies[1][0]["metrics"]["rqt"] == {"1001": [], "1002": [], "1003": []}


@mocketize
def test_backfill_preserves_buffered_samples(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1003)):
        HireFire.configuration.buffer.sample("web", "rqt", 5)
        session.report()

    assert bodies[1][0]["metrics"]["rqt"] == {"1001": [], "1002": [], "1003": [5.0, 1]}


@mocketize
def test_seconds_from_a_failed_dispatch_are_reclaimed_by_the_next_success(session):
    stub_lease()
    Entry.register(
        Entry.POST,
        INGEST_URL,
        Response(status=200),
        Response(status=500),
        Response(status=200),
    )
    bodies = IngestBodies()

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1003)):
        session.report()
    with freeze_time(at(1005)):
        session.report()

    assert sorted(bodies[2][0]["metrics"]["rqt"].keys()) == [
        "1001",
        "1002",
        "1003",
        "1004",
        "1005",
    ]


@mocketize
def test_backfill_is_capped_at_the_limit(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1100)):
        session.report()

    keys = [int(key) for key in bodies[1][0]["metrics"]["rqt"].keys()]
    assert min(keys) == 1100 - payload.RQT_BACKFILL_LIMIT
    assert max(keys) == 1100
    assert len(keys) == payload.RQT_BACKFILL_LIMIT + 1


@mocketize
def test_lease_unauthorized_does_not_log_error(caplog, session):
    caplog.set_level(logging.ERROR)
    Entry.single_register(Entry.POST, LEASE_URL, status=401)
    bodies = capture_ingest_bodies()

    configure_workers_only()
    job_queue_pass(session)
    session.report()

    assert bodies == []
    assert "401" not in caplog.text


@mocketize
def test_web_buffer_discarded_on_unauthorized(caplog, session):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = ingest_statuses(401, 200)

    configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        session.report()

    assert "web" not in HireFire.configuration.buffer.flush()
    assert seconds_claimed_at(session, 1002, bodies) == ["1001", "1002"]
    assert "Dispatch error" not in caplog.text


@mocketize
def test_web_buffer_repopulated_on_dispatch_failure(session):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=500)

    configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        session.report()

    assert HireFire.configuration.buffer.flush()["web"]["rqt"][1000] == {
        "sum": 7.0,
        "count": 1,
    }


@mocketize
def test_oversized_payload_is_dropped_without_a_request(caplog, session):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()
    with freeze_time(at(1000)):
        inject_oversized_series()
        session.report()

    assert bodies == []
    assert "web" not in HireFire.configuration.buffer.flush()
    assert "Dropped metrics payload" in caplog.text


@mocketize
def test_an_oversized_payload_without_web_data_drops_without_touching_the_watermark(
    caplog, session
):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=False)
    bodies = capture_ingest_bodies()

    inject_oversized_series(name="worker", strategy="jql")
    session.report()

    assert bodies == []
    assert "Dropped metrics payload" in caplog.text


@mocketize
def test_oversized_drop_advances_the_watermark_past_the_hole(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1010)):
        inject_oversized_series()
        session.report()
    with freeze_time(at(1012)):
        session.report()

    assert len(bodies) == 2
    assert sorted(bodies[1][0]["metrics"]["rqt"].keys()) == ["1011", "1012"]


@mocketize
def test_combined_web_and_worker_dispatch(session):
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    with freeze_time(at(1000)):
        configure_web_and_workers()
        HireFire.configuration.buffer.sample("web", "rqt", 5)
        job_queue_pass(session)
        session.report()

    entries = bodies[0]
    assert any(entry["name"] == "web" and "metrics" in entry for entry in entries)
    assert any(
        entry["name"] == "worker" and entry.get("metrics", {}).get("jql")
        for entry in entries
    )


@mocketize
def test_dispatch_tick_does_not_run_job_queue_sampling(session):
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()
    sampled = []

    def sampler():
        sampled.append(True)
        return 42

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("web")
        HireFire.configuration.dyno("worker", sampler)
        HireFire.configuration.buffer.sample("web", "rqt", 5)
        session.report()

    assert [entry["name"] for entry in bodies[0]] == ["web"]
    assert sampled == []


@mocketize
def test_job_queue_tick_samples_without_dispatching_and_a_later_tick_delivers_it(
    session,
):
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("worker", lambda: 42)

        job_queue_pass(session)
        assert bodies == []

        session.report()

    assert len(bodies) == 1
    assert any(
        entry["name"] == "worker" and entry.get("metrics", {}).get("jql")
        for entry in bodies[0]
    )


@mocketize
def test_hyphen_dyno_samples_and_dispatches_name_as_is(monkeypatch, caplog, session):
    stub_lease(
        granted=True,
        plan={
            "version": 1,
            "job_queues": [
                {
                    "name": "worker-latency",
                    "strategy": "jql",
                    "adapter": None,
                    "queues": [],
                    "options": {},
                }
            ],
        },
    )
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "worker-latency-6d7f788ddb-cdct6")

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("worker-latency", lambda: 7)
        job_queue_pass(session)
        session.report()

    names = [entry["name"] for entry in bodies[0]]
    assert "worker-latency" in names
    assert "worker" not in names
    entry = next(e for e in bodies[0] if e["name"] == "worker-latency")
    assert entry["metrics"]["jql"]["1000"] == 7
    assert "local sampler is ignored" not in caplog.text


@mocketize
def test_lease_granted_dispatches_workers(session):
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    configure_workers_only()
    job_queue_pass(session)
    session.report()

    assert any(
        entry["name"] == "worker" and entry.get("metrics", {}).get("jql")
        for entry in bodies[0]
    )


@mocketize
def test_sample_trace_attached_when_grant_trace_true(session):
    stub_lease(granted=True, trace=True)
    bodies = capture_ingest_bodies()

    configure_workers_only()
    job_queue_pass(session)
    session.report()

    assert len(bodies) == 1
    entry = bodies[0][0]
    assert "sample_trace" in entry, "sample_trace attaches to first process report"
    assert "wave_ms" in entry["sample_trace"]
    assert isinstance(entry["sample_trace"]["ops"], list)
    assert len(entry["sample_trace"]["ops"]) == 2, "one op per plan job_queue entry"
    assert all(
        op.get("strategy") == "jql" and "ms" in op
        for op in entry["sample_trace"]["ops"]
    )
    for other in bodies[0][1:]:
        assert "sample_trace" not in other


@mocketize
def test_a_session_halted_during_a_round_samples_no_further_entry_and_traces_only_what_ran(
    monkeypatch, session
):
    stub_lease(granted=True, trace=True)
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "web.1")
    sampled = []

    def worker():
        sampled.append("worker")
        session.halt(handoff=True)
        return 42

    HireFire.configuration.dyno("worker", worker)
    HireFire.configuration.dyno("mailer", lambda: sampled.append("mailer"))

    job_queue_pass(session)
    session.flush()

    assert sampled == ["worker"]
    assert len(bodies) == 1
    assert len(bodies[0][0]["sample_trace"]["ops"]) == 1
    assert not any("jql" in row["metrics"] for row in bodies[0])


@mocketize
def test_oversized_sample_trace_is_stripped_so_metrics_still_ship(caplog, session):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=True, trace=True)
    bodies = capture_ingest_bodies()
    configure_workers_only()
    trace = {"wave_ms": 1.0, "ops": [], "pad": "x" * Session.PAYLOAD_SIZE_LIMIT}

    with patch.object(Sampler, "round", return_value=trace), freeze_time(at(1000)):
        job_queue_pass(session)
        HireFire.configuration.buffer.sample("worker", "jql", 7)
        session.report()

    assert bodies == [[{"name": "worker", "metrics": {"jql": {"1000": 7}}}]]
    assert "Dropped metrics payload" not in caplog.text


@mocketize
def test_a_trace_is_kept_in_a_payload_of_exactly_the_limit_and_stripped_one_byte_over(
    caplog,
):
    caplog.set_level(logging.ERROR)
    limit = Session.PAYLOAD_SIZE_LIMIT
    stub_lease(granted=True, trace=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    configure_workers_only()

    def send_with_pad(pad):
        trace = {"wave_ms": 1.0, "ops": [], "pad": "x" * pad}
        with patch.object(Sampler, "round", return_value=trace):
            run = Session(HireFire.configuration)
            job_queue_pass(run)
            HireFire.configuration.buffer.sample("worker", "jql", 1)
            run.report()
        return ingest_texts()[-1]

    with freeze_time(at(1000)):
        base = len(send_with_pad(0))
        exact = send_with_pad(limit - base)
        over = send_with_pad(limit - base + 1)

    assert len(exact) == limit
    assert "sample_trace" in exact
    assert over == '[{"name":"worker","metrics":{"jql":{"1000":1}}}]'
    assert "Dropped metrics payload" not in caplog.text


@mocketize
def test_a_trace_that_was_stripped_is_not_sent_with_a_later_payload(session):
    stub_lease(granted=True, trace=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    configure_workers_only()
    pad = "x" * (Session.PAYLOAD_SIZE_LIMIT - 1_500)

    with patch.object(Sampler, "round", return_value={"ops": [], "pad": pad}):
        with freeze_time(at(1000)):
            job_queue_pass(session)
            HireFire.configuration.buffer.sample("worker", "jql", 1)
            HireFire.configuration.buffer.sample("p" * 2_000, "jql", 1)
            session.report()
        with freeze_time(at(1002)):
            HireFire.configuration.buffer.sample("worker", "jql", 2)
            session.report()

    first, second = ingest_texts()
    assert "sample_trace" not in first
    assert "p" * 2_000 in first
    assert second == '[{"name":"worker","metrics":{"jql":{"1002":2}}}]'


@mocketize
def test_a_trace_sent_with_a_payload_the_server_rejects_as_too_large_is_not_sent_again(
    session,
):
    stub_lease(granted=True, trace=True)
    ingest_statuses(413, 200)
    configure_workers_only()

    with freeze_time(at(1000)):
        job_queue_pass(session)
        session.report()
    with freeze_time(at(1002)):
        HireFire.configuration.buffer.sample("worker", "jql", 2)
        session.report()

    first, second = ingest_texts()
    assert "sample_trace" in first
    assert "sample_trace" not in second


@mocketize
def test_the_trace_of_a_round_that_ends_during_a_dispatch_is_sent_with_the_next_payload(
    monkeypatch, session
):
    stub_lease(granted=True, trace=True)
    configure_workers_only()
    bodies = []
    in_request = threading.Event()
    release = threading.Event()

    def submit(body):
        bodies.append(body)
        if len(bodies) == 1:
            in_request.set()
            assert release.wait(5)
        return answer()

    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with ingest(submit):
        job_queue_pass(session)
        dispatch = threading.Thread(target=session.report)
        dispatch.start()
        assert in_request.wait(5)
        clock[0] += 100
        job_queue_pass(session)
        release.set()
        dispatch.join(5)
        clock[0] += 100
        session.report()

    assert "sample_trace" in bodies[0]
    assert "sample_trace" in bodies[1]


@mocketize
def test_a_round_that_is_still_running_past_its_limit_is_reported_once(
    monkeypatch, caplog, session
):
    stub_lease(granted=True)
    entered = threading.Event()
    release = threading.Event()

    def worker():
        entered.set()
        assert release.wait(5)
        return 1

    HireFire.configuration.dyno("worker", worker)
    HireFire.configuration.dyno("mailer", lambda: 1)
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    session.renew()
    sampler = threading.Thread(target=session.sample)
    sampler.start()
    assert entered.wait(5)
    clock[0] += Session.SAMPLE_ROUND_LIMIT + 1
    for _ in range(3):
        session.renew()
    release.set()
    sampler.join(5)

    line = "A job queue sample round has run for more than 60 seconds."
    assert caplog.text.count(line) == 1


@mocketize
def test_a_round_that_runs_past_its_limit_gives_up_the_lease_and_its_samples(
    monkeypatch, caplog, session
):
    stub_lease(granted=True)
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def worker():
        clock[0] += Session.SAMPLE_ROUND_LIMIT + 1
        session.renew()
        return 42

    def mailer():
        pytest.fail("an entry was sampled after the lease was given up")

    HireFire.configuration.dyno("worker", worker)
    HireFire.configuration.dyno("mailer", mailer)

    job_queue_pass(session)

    assert lease_requests() == 1
    line = (
        "A job queue sample round has run for more than 60 seconds. "
        "The lease is released so that another process can sample."
    )
    assert caplog.text.count(line) == 1
    assert HireFire.configuration.buffer.flush() == {}

    session.renew()
    assert lease_requests() == 2


@mocketize
def test_a_round_inside_its_limit_keeps_the_lease(monkeypatch, caplog, session):
    assert Session.SAMPLE_ROUND_LIMIT == 60
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def worker():
        clock[0] += Session.SAMPLE_ROUND_LIMIT
        session.renew()
        return 42

    HireFire.configuration.dyno("worker", worker)

    job_queue_pass(session)
    session.report()

    assert "The lease is released" not in caplog.text
    entry = next(entry for entry in bodies[0] if entry["name"] == "worker")
    assert list(entry["metrics"]["jql"].values()) == [42]


@mocketize
def test_sample_trace_absent_without_grant_trace(session):
    stub_lease(granted=True, trace=False)
    bodies = capture_ingest_bodies()

    configure_workers_only()
    job_queue_pass(session)
    session.report()

    assert bodies
    for entry in bodies[0]:
        assert "sample_trace" not in entry


@mocketize
def test_verbose_logs_sample_timings_without_server_trace(monkeypatch, caplog, session):
    monkeypatch.setenv("HIREFIRE_VERBOSE", "1")
    caplog.set_level(logging.INFO)
    stub_lease(granted=True, trace=False)

    configure_workers_only()
    job_queue_pass(session)

    assert "sample_job_queues wave_ms=" in caplog.text
    assert "sample adapter=" in caplog.text


@mocketize
def test_lease_denied_skips_worker_collection(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_workers_only()
    job_queue_pass(session)
    session.report()

    assert bodies == []


@mocketize
def test_dispatches_cpu_samples_in_the_nested_format(monkeypatch, session):
    bodies = capture_ingest_bodies()
    with (
        patch.object(Usage, "available_cpus", return_value=1.0),
        patch.object(
            Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (0.5, "cgroup_v2")]
        ),
    ):
        configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            session.report()
        with freeze_time(at(1001)):
            session.report()

    assert len(bodies) == 1
    entry = bodies[0][0]
    assert entry["name"] == "clock"
    assert entry["metrics"] == {"cpu": {"1001": 50.0}}


@mocketize
def test_dispatches_jqs_and_wrk_as_sibling_bare_numbers(session):
    stub_lease()
    bodies = capture_ingest_bodies()
    configure_workers_only()

    with freeze_time(at(2500)):
        HireFire.configuration.buffer.sample("worker", "jqs", 12)
        HireFire.configuration.buffer.sample("worker", "wrk", 3)
        session.flush()

    entry = next(e for e in bodies[-1] if e["name"] == "worker")
    assert entry["metrics"] == {"jqs": {"2500": 12}, "wrk": {"2500": 3}}


@mocketize
def test_cpu_first_tick_seeds_baseline_without_dispatching(monkeypatch, session):
    bodies = capture_ingest_bodies()
    with (
        patch.object(Usage, "available_cpus", return_value=1.0),
        patch.object(Usage, "reading", return_value=(0.0, "cgroup_v2")),
    ):
        configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            session.report()

    assert bodies == []


@mocketize
def test_cpu_samples_are_not_repopulated_on_dispatch_failure(monkeypatch, session):
    Entry.single_register(Entry.POST, INGEST_URL, status=500)
    with (
        patch.object(Usage, "available_cpus", return_value=1.0),
        patch.object(
            Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (0.5, "cgroup_v2")]
        ),
    ):
        configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            session.report()
        with freeze_time(at(1001)):
            session.report()

    data = HireFire.configuration.buffer.flush()
    assert "clock" not in data or "cpu" not in data.get("clock", {})


@mocketize
def test_non_web_process_does_not_heartbeat_the_web_name(monkeypatch, session):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "worker.1")
    HireFire.configuration.dyno("web")

    session.report()

    assert bodies == []


@mocketize
def test_non_web_process_still_delivers_real_web_samples(monkeypatch, session):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "worker.1")
    HireFire.configuration.dyno("web")

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 12)
        session.report()

    assert bodies[0][0]["metrics"]["rqt"] == {"1000": [12.0, 1]}


@mocketize
def test_matching_identity_keeps_heartbeat_and_backfill(monkeypatch, session):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "web.1")
    HireFire.configuration.dyno("web")

    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1002)):
        session.report()

    assert bodies[0][0]["metrics"]["rqt"] == {"1000": []}
    assert bodies[1][0]["metrics"]["rqt"] == {"1001": [], "1002": []}


@mocketize
def test_unresolved_identity_does_not_synthesize_liveness(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    config = HireFire.configuration
    config.dyno("web")
    config.dispatcher
    with freeze_time(at(1000)):
        session.report()

    assert bodies == []


@mocketize
def test_always_on_cpu_uses_identity_name_through_the_tick(monkeypatch, session):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")

    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1001)):
        session.report()

    names = [entry["name"] for entry in bodies[-1]]
    assert names == ["web"]
    assert bodies[-1][0].get("metrics", {}).get("cpu") is not None


@mocketize
def test_forked_child_restarts_the_dispatcher():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    assert dispatcher.start()

    child_pid = os.getpid() + 1
    with patch("os.getpid", return_value=child_pid):
        assert not dispatcher.running()
        assert dispatcher.start()
        assert dispatcher.running()
        dispatcher.stop()


@mocketize
def test_a_dispatch_pass_goes_on_when_the_lease_request_fails(caplog, session):
    bodies = capture_ingest_bodies()
    refused = RequestError("Network error (ConnectionRefusedError: refused).")

    with (
        freeze_time(at(1000)),
        patch.object(Client, "request_lease", side_effect=refused),
    ):
        configure_web_and_workers()
        HireFire.configuration.buffer.sample("web", "rqt", 12)
        job_queue_pass(session)
        session.report()

    assert "Lease request error: RequestError: Network error" in caplog.text
    assert len(bodies) == 1


@mocketize
def test_the_first_failed_lease_request_is_logged_and_later_ones_once_a_minute_with_their_count(
    caplog, session
):
    Entry.single_register(Entry.POST, LEASE_URL, status=500)
    configure_web_and_workers()

    renew_each_second(session, range(1000, 1060))
    assert lease_requests() == 4
    assert caplog.text.count("Lease request error") == 1
    assert (
        "[HireFire] Lease request error: RequestError: "
        "Lease request failed with 500 status." in messages(caplog)
    )

    renew_each_second(session, range(1060, 1126))
    assert re.findall(r"\(\d+ failed attempts in a row\)", caplog.text) == [
        "(5 failed attempts in a row)",
        "(9 failed attempts in a row)",
    ]


@mocketize
def test_a_recovery_after_several_failed_lease_requests_is_logged_once(caplog, session):
    Entry.register(
        Entry.POST,
        LEASE_URL,
        Response(status=500),
        Response(status=500),
        Response(status=200, headers={"HireFire-Lease-Granted": "false"}),
    )
    configure_web_and_workers()

    renew_each_second(session, range(1000, 1051))

    line = "Lease request recovered after 2 failed attempts."
    assert caplog.text.count(line) == 1
    assert caplog.text.count("Lease request error") == 1


@mocketize
def test_a_lease_request_that_is_not_due_does_not_count_as_a_recovery(caplog, session):
    Entry.single_register(Entry.POST, LEASE_URL, status=500)
    configure_web_and_workers()

    renew_each_second(session, range(1000, 1030))

    assert lease_requests() == 2
    assert "recovered" not in caplog.text


@mocketize
def test_tick_dispatches_when_a_sampler_raises(caplog, monkeypatch, session):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "web.1")

    def boom():
        raise RuntimeError("Redis down")

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("worker", boom)
        job_queue_pass(session)
        session.report()

    assert len(bodies) == 1
    assert [entry["name"] for entry in bodies[0]] == ["web"]
    assert "Redis down" in caplog.text


@mocketize
def test_started_thread_dispatches_until_stopped():
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    dispatcher.start()

    deadline = time.time() + 5
    while not any(path == "/metrics/ingest" for path in request_paths()):
        assert time.time() < deadline, "dispatcher never POSTed to /metrics/ingest"
        time.sleep(0.05)
    assert dispatcher.running()

    dispatcher.stop()
    assert not dispatcher.running()


@mocketize
def test_a_hung_worker_sampler_does_not_stall_web_dispatch():
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    bodies = IngestBodies()

    entered = threading.Event()
    release = threading.Event()

    def hung_sampler():
        entered.set()
        release.wait(5)
        return 1

    config = HireFire.configuration
    config.dyno("web")
    config.dyno("worker", hung_sampler)
    dispatcher = config.dispatcher
    dispatcher.start()

    try:
        assert entered.wait(timeout=5), "worker sampler never started"
        config.buffer.sample("web", "rqt", 5)
        wait_until(
            lambda: any(entry["name"] == "web" for body in bodies for entry in body)
        )
    finally:
        release.set()
        dispatcher.stop()


@mocketize
def test_stop_flushes_the_buffer():
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()

    with freeze_time(at(1000)):
        assert dispatcher.start()
        wait_until(lambda: len(bodies) == 1)
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        dispatcher.stop()

    assert len(bodies) == 2
    assert bodies[1][0]["metrics"]["rqt"] == {"1000": [7.0, 1]}


@mocketize
def test_a_stop_closes_the_connection_of_each_loop(monkeypatch):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    closed = []
    monkeypatch.setattr(Client, "close", lambda client: closed.append(client))
    dispatcher = configure_workers_only()

    assert dispatcher.start()
    wait_until(lambda: loop_threads("hirefire-lease"))
    dispatcher.stop()

    wait_until(lambda: len(closed) == 2)
    assert closed[0] is not closed[1]


@mocketize
def test_web_only_dispatch_never_requests_a_lease(session):
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()

    assert "/metrics/lease" not in request_paths()


@mocketize
def test_tick_survives_a_payload_build_error(caplog, session):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        with patch.object(payload, "build", side_effect=RuntimeError("boom")):
            session.report()

    assert bodies == []
    assert "Dispatch error: RuntimeError: boom" in caplog.text
    assert HireFire.configuration.buffer.flush()["web"]["rqt"][1000] == {
        "sum": 7.0,
        "count": 1,
    }


def stub_ingest_with_dispatch_frequency(value):
    Entry.single_register(
        Entry.POST,
        INGEST_URL,
        status=200,
        headers={"HireFire-Dispatch-Frequency": str(value)},
    )


@mocketize
def test_dispatch_frequency_defaults_to_one_without_the_header(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1001)):
        session.report()

    assert len(bodies) == 2


@mocketize
def test_honors_a_server_supplied_dispatch_frequency(session):
    stub_lease()
    stub_ingest_with_dispatch_frequency(5)
    bodies = IngestBodies()

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1002)):
        session.report()
    with freeze_time(at(1004)):
        session.report()
    with freeze_time(at(1005)):
        session.report()

    assert len(bodies) == 2


@pytest.mark.parametrize("status", [401, 413])
@mocketize
def test_honors_the_dispatch_frequency_of_a_rejected_request(status, session):
    stub_lease()
    Entry.single_register(
        Entry.POST,
        INGEST_URL,
        status=status,
        headers={"HireFire-Dispatch-Frequency": "30"},
    )
    bodies = IngestBodies()

    configure_web_only()
    with freeze_time(at(1000)):
        session.report()
    with freeze_time(at(1029)):
        session.report()
    assert len(bodies) == 1
    with freeze_time(at(1030)):
        session.report()

    assert len(bodies) == 2


@mocketize
def test_clamps_an_over_large_dispatch_frequency_to_the_maximum(session):
    stub_lease()
    stub_ingest_with_dispatch_frequency(Session.MAX_DISPATCH_FREQUENCY + 100)

    configure_web_only()

    assert attempts(session, range(1000, 1061)) == [1000, 1030, 1060]


@mocketize
def test_ignores_a_non_positive_dispatch_frequency(session):
    assert Session.DEFAULT_DISPATCH_FREQUENCY == 1
    stub_lease()
    stub_ingest_with_dispatch_frequency(0)

    configure_web_only()

    assert attempts(session, range(1000, 1003)) == [1000, 1001, 1002]


@pytest.mark.parametrize("value", ["10.0", "10abc", "+10", " ", "abc"])
@mocketize
def test_ignores_a_dispatch_frequency_that_is_not_a_whole_number(value, session):
    stub_lease()
    stub_ingest_with_dispatch_frequency(value)

    configure_web_only()

    assert attempts(session, range(1000, 1003)) == [1000, 1001, 1002]


@mocketize
def test_dispatch_pacing_follows_the_monotonic_clock_not_the_wall_clock(session):
    stub_lease()
    bodies = capture_ingest_bodies()

    configure_web_only()
    mono = [500.0]
    with freeze_time(at(1000)), patch("time.monotonic", side_effect=lambda: mono[0]):
        session.report()
        mono[0] = 502.0
        session.report()

    assert len(bodies) == 2


@mocketize
def test_dispatch_failure_without_web_data_does_not_repopulate(caplog, session):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=500)

    configure_workers_only()
    job_queue_pass(session)
    session.report()

    assert "web" not in HireFire.configuration.buffer.flush()
    assert "Dispatch error" in caplog.text


@mocketize
def test_413_advances_watermark_without_repopulate(caplog, session):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = ingest_statuses(413, 200)

    configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        session.report()

    assert "web" not in HireFire.configuration.buffer.flush()
    assert seconds_claimed_at(session, 1002, bodies) == ["1001", "1002"]
    assert "Dropped metrics payload" in caplog.text


@mocketize
def test_encode_omits_non_finite_rqt_mean(caplog, session):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = capture_ingest_bodies()
    configure_web_only()

    with freeze_time(at(1000)):
        buffer = HireFire.configuration.buffer
        with buffer._mutex:
            buffer._metrics["web"] = {
                "rqt": {
                    1000: {"sum": float("inf"), "count": 1},
                    999: {"sum": 10.0, "count": 1},
                }
            }
        session.report()

    assert len(bodies) >= 1
    rqt = bodies[0][0]["metrics"]["rqt"]
    assert "1000" not in rqt
    assert rqt["999"] == [10.0, 1]
    assert "Omitting rqt second" in caplog.text


@mocketize
def test_stop_without_flush_discards_buffer():
    stub_lease()
    capture_ingest_bodies()
    dispatcher = configure_web_only()
    assert dispatcher.start()
    HireFire.configuration.buffer.sample("web", "rqt", 42)
    dispatcher.stop(flush=False)
    assert HireFire.configuration.buffer.flush() == {}


@mocketize
def test_stop_after_abandon_does_not_post_buffered_samples():
    stub_lease()
    dispatcher = configure_web_only()
    assert dispatcher.start()
    HireFire.configuration.buffer.sample("web", "rqt", 7)
    dispatcher.abandon_inherited_state()
    Mocket.reset()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    bodies_after = capture_ingest_bodies()
    assert dispatcher.stop() is False
    assert len(bodies_after) == 0
    assert HireFire.configuration.buffer.flush() == {}


@mocketize
def test_unknown_plan_adapter_skips_without_local_fallback(caplog, session):
    caplog.set_level(logging.ERROR)
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jql",
                "adapter": "nope",
                "queues": [],
                "options": {},
            }
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()
    HireFire.configuration.dyno("worker", lambda: 42)
    job_queue_pass(session)
    session.report()
    assert len(bodies) == 0
    assert "Unknown plan adapter" in caplog.text


@mocketize
def test_a_full_plan_of_unknown_adapters_warns_once_per_entry(caplog, session):
    caplog.set_level(logging.ERROR)
    assert Once.LIMIT == Lease.MAX_JOB_QUEUES
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": f"worker_{i}",
                "strategy": "jql",
                "adapter": "nope",
                "queues": [],
                "options": {},
            }
            for i in range(Lease.MAX_JOB_QUEUES)
        ],
    }
    stub_lease(granted=True, plan=plan)
    HireFire.configuration.dyno("other", lambda: 0)

    with freeze_time(at(1000)):
        job_queue_pass(session)
    with freeze_time(at(1015)):
        job_queue_pass(session)

    assert caplog.text.count("Unknown plan adapter") == Lease.MAX_JOB_QUEUES


@mocketize
def test_strategy_only_plan_uses_local_sampler(caplog, session):
    caplog.set_level(logging.WARNING)
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jqs",
                "adapter": None,
                "queues": [],
                "options": {},
            }
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()
    HireFire.configuration.dyno("worker", lambda: 7)
    job_queue_pass(session)
    session.report()

    entry = next(e for e in bodies[0] if e["name"] == "worker")
    assert list(entry["metrics"]["jqs"].values())[0] == 7
    assert "UI adapter is configured" not in caplog.text


@mocketize
def test_strategy_only_plan_awaits_an_async_local_sampler_off_the_main_thread(session):
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jqs",
                "adapter": None,
                "queues": [],
                "options": {},
            }
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()

    async def sampler():
        return 7

    HireFire.configuration.dyno("worker", sampler)
    thread = threading.Thread(target=lambda: job_queue_pass(session))
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    session.report()

    entry = next(e for e in bodies[0] if e["name"] == "worker")
    assert list(entry["metrics"]["jqs"].values())[0] == 7


@mocketize
def test_strategy_only_plan_reports_lease_name_not_local_dyno_spelling(session):
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jqs",
                "adapter": None,
                "queues": [],
                "options": {},
            }
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()
    HireFire.configuration.dyno("Worker", lambda: 7)
    job_queue_pass(session)
    session.report()

    names = [e["name"] for e in bodies[0]]
    assert "worker" in names
    assert "Worker" not in names


@mocketize
def test_empty_string_adapter_uses_local_strategy_sampler(session):
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jqs",
                "adapter": "",
                "queues": [],
                "options": {},
            }
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()
    HireFire.configuration.dyno("worker", lambda: 11)
    job_queue_pass(session)
    session.report()

    entry = next(e for e in bodies[0] if e["name"] == "worker")
    assert list(entry["metrics"]["jqs"].values())[0] == 11


@mocketize
def test_empty_plan_with_local_samplers_still_holds_lease(caplog, session):
    stub_lease(granted=True, plan={"version": 1, "job_queues": []})
    HireFire.configuration.dyno("worker", lambda: 5)

    job_queue_pass(session)

    assert "Lease grant dropped" not in caplog.text


@mocketize
def test_executable_plan_without_local_dyno_holds_lease_and_samples(
    load_macros, caplog, session
):
    load_macros(celery=fake_macro(job_queue_latency=lambda *queues, **options: 4.2))
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jql",
                "adapter": "celery",
                "queues": ["default"],
                "options": {},
            }
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()

    job_queue_pass(session)
    session.report()

    assert "Lease grant dropped" not in caplog.text
    entry = next(e for e in bodies[0] if e["name"] == "worker")
    assert list(entry["metrics"]["jql"].values())[0] == 4.2


@mocketize
def test_a_grant_is_dropped_when_no_sampler_exists_and_the_adapter_is_unknown(
    caplog, session
):
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jql",
                "adapter": "nope",
                "queues": [],
                "options": {},
            }
        ],
    }
    stub_lease(granted=True, plan=plan)

    job_queue_pass(session)

    assert "Lease grant dropped: this process cannot sample the plan" in caplog.text
    assert "Unknown plan adapter" not in caplog.text


@mocketize
def test_hold_demotion_logs_and_web_dispatch_continues(monkeypatch, caplog, session):
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "x",
                "strategy": "jqs",
                "adapter": "unknown_adapter",
                "queues": [],
            }
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "web.1")
    HireFire.configuration.buffer.sample("web", "rqt", 3)

    job_queue_pass(session)
    assert "Lease grant dropped" in caplog.text

    session.report()
    assert len(bodies) == 1
    assert [entry["name"] for entry in bodies[0]] == ["web"]


@mocketize
def test_payload_size_limit_is_131072_with_strict_greater_drop(caplog, session):
    caplog.set_level(logging.ERROR)
    limit = Session.PAYLOAD_SIZE_LIMIT
    assert limit == 131_072
    stub_lease()
    posted = []
    configure_web_only()

    def submit(body):
        posted.append(body)
        return answer()

    with ingest(submit):
        with freeze_time(at(1000)):
            HireFire.configuration.buffer.sample("web", "rqt", 1)
            with patch("json.dumps", return_value="e" * limit):
                session.report()
        assert posted == ["e" * limit]
        assert "Dropped metrics payload" not in caplog.text

        with freeze_time(at(1001)):
            HireFire.configuration.buffer.sample("web", "rqt", 1)
            with patch("json.dumps", return_value="o" * (limit + 1)):
                session.report()

    assert len(posted) == 1
    assert (
        f"Dropped metrics payload: {limit + 1} bytes exceeds the {limit}-byte limit."
        in caplog.text
    )


@mocketize
def test_seven_sample_waves_of_a_full_plan_with_the_longest_names_ship_in_one_payload(
    caplog, session
):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = capture_ingest_bodies()
    buffer = HireFire.configuration.buffer
    names = [
        f"worker_{i:03d}".ljust(identity.MAX_NAME_BYTES, "x")
        for i in range(Lease.MAX_JOB_QUEUES)
    ]

    with freeze_time(at(1000)) as frozen:
        for second in (1000, 1005, 1010, 1015, 1020, 1025, 1030):
            frozen.move_to(at(second))
            for name in names:
                buffer.sample(name, "jqs", 1234)
                buffer.sample(name, "wrk", 12)
        session.report()

    assert len(bodies) == 1
    assert [entry["name"] for entry in bodies[0]] == names
    assert all(
        [len(series) for series in entry["metrics"].values()] == [7, 7]
        for entry in bodies[0]
    )
    sizes = [
        len(request.body)
        for request in Mocket.request_list()
        if request.path == "/metrics/ingest"
    ]
    assert sizes[0] > 65_536
    assert "Dropped metrics payload" not in caplog.text


@mocketize
def test_encode_omits_invalid_non_rqt_values(session):
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()
    limit = payload.METRIC_VALUE_LIMIT
    configure_web_only()
    HireFire.configuration.dyno("worker", lambda: 1)
    with freeze_time(at(1000)):
        buffer = HireFire.configuration.buffer
        with buffer._mutex:
            buffer._metrics["worker"] = {
                "jql": {
                    1000: float("nan"),
                    999: float("inf"),
                    998: -1.0,
                    997: limit + 1,
                    995: 4.5,
                },
                "cpu": {1000: -0.1, 999: 12.0},
            }
            buffer._metrics["web"] = {"rqt": {1000: {"sum": 1.0, "count": 1}}}
        session.report()
    assert len(bodies) >= 1
    worker = next(e for e in bodies[0] if e["name"] == "worker")
    jql = worker["metrics"].get("jql", {})
    cpu = worker["metrics"].get("cpu", {})
    assert "1000" not in jql
    assert "999" not in jql
    assert "998" not in jql
    assert "997" not in jql
    assert jql.get("995") == 4.5
    assert "1000" not in cpu
    assert cpu.get("999") == 12.0


@mocketize
def test_a_start_in_a_forked_child_drops_what_the_parent_buffered_and_begins_at_the_current_second(
    monkeypatch, caplog
):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "web.1")
    dispatcher = HireFire.configuration.dispatcher
    parent_cpu = HireFire.configuration.active_cpu_sources()[0]

    with freeze_time(at(1000)) as clock:
        assert dispatcher.start()
        wait_until(lambda: len(bodies) == 1)
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        with patch("os.getpid", return_value=os.getpid() + 1):
            clock.move_to(at(1030))
            assert dispatcher.start()
            wait_until(lambda: len(bodies) == 2)
            dispatcher.stop(flush=False)

    assert caplog.text.count("Starting dispatcher.") == 2
    assert list(bodies[1][0]["metrics"]["rqt"]) == ["1030"]
    assert HireFire.configuration.active_cpu_sources()[0] is not parent_cpu


@mocketize
def test_jql_not_repopulated_on_dispatch_failure(caplog, session):
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=500)
    caplog.set_level(logging.ERROR)

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("worker", lambda: 3)
        job_queue_pass(session)
        session.report()

        assert HireFire.configuration.buffer.flush() == {}
        assert "Dispatch error" in caplog.text


@mocketize
def test_nested_payload_merges_rqt_and_cpu_under_one_name(monkeypatch, session):
    monkeypatch.setenv("DYNO", "web.1")
    bodies = capture_ingest_bodies()
    with (
        patch.object(Usage, "available_cpus", return_value=1.0),
        patch.object(
            Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (0.5, "cgroup_v2")]
        ),
    ):
        HireFire.configuration.dyno("web")

        with freeze_time(at(1000)):
            session.report()
        with freeze_time(at(1001)):
            HireFire.configuration.buffer.sample("web", "rqt", 12)
            session.report()

    entry = next(e for e in bodies[-1] if e["name"] == "web")
    assert "rqt" in entry["metrics"]
    assert "cpu" in entry["metrics"]


@mocketize
def test_wire_payload_nested_multi_strategy_shape(monkeypatch, session):
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "web.1")

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("web")
        HireFire.configuration.dyno("worker", lambda: 3)

        HireFire.configuration.buffer.sample("web", "rqt", 12)
        HireFire.configuration.buffer.sample("web", "cpu", 25.0)
        job_queue_pass(session)
        session.report()

    assert len(bodies) >= 1
    payload = bodies[0]
    web = next(e for e in payload if e["name"] == "web")
    worker = next(e for e in payload if e["name"] == "worker")

    assert web["metrics"]["rqt"] == {"1000": [12.0, 1]}
    assert web["metrics"]["cpu"] == {"1000": 25.0}
    assert "jql" in worker["metrics"]
    for entry in payload:
        assert sorted(entry.keys()) == ["metrics", "name"]
        assert all(isinstance(k, str) for k in entry["metrics"].keys())


@mocketize
def test_the_first_start_sends_what_was_buffered_before_it():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 5)

    assert dispatcher.start()

    wait_until(lambda: sent(bodies, "web", "rqt", [5.0, 1]))
    dispatcher.stop()


@mocketize
def test_start_after_parent_stop_reinitializes_inherited_state():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()
    assert dispatcher.start()
    first_cpu = HireFire.configuration.active_cpu_sources()[0]

    dispatcher.stop(flush=False)

    with patch("os.getpid", return_value=os.getpid() + 1):
        assert dispatcher.start()
        assert HireFire.configuration.active_cpu_sources()[0] is not first_cpu
        dispatcher.stop()


@mocketize
def test_abandon_inherited_state_resets_always_on_sources(monkeypatch):
    stub_lease()
    monkeypatch.setenv("DYNO", "web.1")
    dispatcher = configure_web_only()
    cpu = HireFire.configuration.active_cpu_sources()[0]
    dispatcher.abandon_inherited_state()
    assert HireFire.configuration.active_cpu_sources()[0] is not cpu


@mocketize
def test_abandon_inherited_state_calls_reinit_macros_after_fork():
    dispatcher = configure_web_only()
    with patch.object(plan, "reinit_macros_after_fork") as reinit:
        dispatcher.abandon_inherited_state()
        reinit.assert_called_once_with(HireFire.configuration.logger)


@mocketize
def test_start_after_fork_calls_reinit_macros_after_fork():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    assert dispatcher.start()

    with (
        patch("os.getpid", return_value=os.getpid() + 1),
        patch.object(plan, "reinit_macros_after_fork") as reinit,
    ):
        assert dispatcher.start()
        reinit.assert_called_once_with(HireFire.configuration.logger)
        dispatcher.stop()


@mocketize
def test_concurrent_start_during_stop_is_rejected_then_retryable_even_if_a_starter_wins_after_stopping_clears():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = HireFire.configuration.dispatcher
    assert dispatcher.start()

    stop_done = threading.Event()
    results: list[bool] = []

    def stopper():
        dispatcher.stop()
        stop_done.set()

    def starter():
        results.append(dispatcher.start())

    stop_thread = threading.Thread(target=stopper)
    starters = [threading.Thread(target=starter) for _ in range(8)]
    stop_thread.start()
    for thread in starters:
        thread.start()
    stop_thread.join(5)
    for thread in starters:
        thread.join(5)

    assert stop_done.is_set()
    dispatcher.stop()
    assert not dispatcher.running()
    assert dispatcher.start()
    assert dispatcher.running()
    dispatcher.stop()


@mocketize
def test_a_halted_session_takes_nothing_from_the_buffer_and_posts_nothing(session):
    stub_lease()
    bodies = capture_ingest_bodies()
    configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 10)

    session.halt()
    session.report()

    assert bodies == []
    assert "rqt" in HireFire.configuration.buffer.flush()["web"]


@mocketize
def test_the_final_flush_of_a_halted_session_sends_what_is_buffered(session):
    bodies = capture_ingest_bodies()
    configure_web_only()

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        session.halt(handoff=True)
        session.flush()

    assert len(bodies) == 1
    assert bodies[0][0]["metrics"]["rqt"] == {"1000": [7.0, 1]}


def test_a_dispatch_that_fails_after_a_halt_for_a_final_flush_keeps_its_samples_for_that_flush(
    session,
):
    bodies = []

    def submit(body):
        bodies.append(json.loads(body))
        session.halt(handoff=True)
        if len(bodies) == 1:
            raise RequestError("Ingest request failed with 500 status.")
        return answer()

    configure_web_only()
    with ingest(submit), freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 10)
        session.report()
        session.flush()

    assert len(bodies) == 2
    assert bodies[1][0]["metrics"]["rqt"] == {"1000": [10.0, 1]}


def test_a_dispatch_that_fails_after_a_halt_without_a_final_flush_drops_its_samples(
    session,
):
    def submit(_body):
        session.halt()
        raise RequestError("Ingest request failed with 500 status.")

    configure_web_only()
    with ingest(submit), freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 10)
        session.report()

    assert HireFire.configuration.buffer.flush() == {}


@mocketize
def test_failed_dispatches_wait_twice_as_long_each_time_up_to_the_longest_interval(
    session,
):
    stub_lease()
    ingest_statuses(500)
    configure_web_only()

    assert attempts(session, range(1000, 1126)) == [
        1000,
        1002,
        1006,
        1014,
        1030,
        1060,
        1090,
        1120,
    ]
    assert Session.MAX_DISPATCH_FREQUENCY == 30
    assert Session.BACKOFF_DOUBLINGS == 5


@mocketize
def test_a_successful_dispatch_ends_the_longer_interval(session):
    stub_lease()
    ingest_statuses(500, 500, 500, 200)
    configure_web_only()

    assert attempts(session, range(1000, 1018)) == [
        1000,
        1002,
        1006,
        1014,
        1015,
        1016,
        1017,
    ]


@mocketize
def test_failed_dispatches_wait_from_the_interval_the_server_set(session):
    stub_lease()
    Entry.register(
        Entry.POST,
        INGEST_URL,
        Response(status=200, headers={"HireFire-Dispatch-Frequency": "5"}),
        Response(status=503),
    )
    configure_web_only()

    assert attempts(session, range(1000, 1071)) == [1000, 1005, 1015, 1035, 1065]


@mocketize
def test_the_first_failed_dispatch_is_logged_and_later_ones_once_a_minute_with_their_count(
    caplog, session
):
    stub_lease()
    ingest_statuses(500)
    configure_web_only()

    attempts(session, range(1000, 1060))
    assert caplog.text.count("Dispatch error") == 1
    assert (
        "[HireFire] Dispatch error: RequestError: "
        "Ingest request failed with 500 status." in messages(caplog)
    )

    attempts(session, range(1060, 1126))
    assert re.findall(r"\(\d+ failed attempts in a row\)", caplog.text) == [
        "(6 failed attempts in a row)",
        "(8 failed attempts in a row)",
    ]


@mocketize
def test_a_token_with_a_line_break_reaches_neither_a_request_nor_the_log(
    monkeypatch, caplog, session
):
    monkeypatch.setenv("HIREFIRE_TOKEN", "s3cr3t-token\nX-Injected: 1")
    configure_web_and_workers()

    with freeze_time(at(1000)):
        session.report()
        session.renew()

    assert Mocket.request_list() == []
    assert messages(caplog) == [
        f"[HireFire] {label} error: RequestError: HireFire token holds a character "
        "that is not printable ASCII. Check HIREFIRE_TOKEN or config.token."
        for label in ("Dispatch", "Lease request")
    ]


def test_a_data_url_that_is_not_a_url_is_named_in_the_dispatch_error(
    monkeypatch, caplog, session
):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "data.hirefire.io")
    configure_web_only()

    attempts(session, range(1000, 1004))

    assert messages(caplog) == [
        "[HireFire] Dispatch error: RequestError: "
        "HIREFIRE_DATA_URL must be an http or https URL with a host."
    ]


@mocketize
def test_a_recovery_after_several_failed_dispatches_is_logged_once(caplog, session):
    stub_lease()
    ingest_statuses(500, 500, 500, 200)
    configure_web_only()

    attempts(session, range(1000, 1021))

    assert caplog.text.count("Dispatch recovered after 3 failed attempts.") == 1
    assert caplog.text.count("Dispatch error") == 1


@mocketize
def test_a_payload_that_the_server_rejects_every_second_is_logged_once_a_minute_and_once_on_recovery(
    caplog, session
):
    stub_lease()
    ingest_statuses(*[413] * 61, 200)
    configure_web_only()

    attempts(session, range(1000, 1060))
    assert caplog.text.count("Dropped metrics payload") == 1

    attempts(session, range(1060, 1063))
    assert caplog.text.count("Dropped metrics payload") == 2
    assert (
        "server rejected (413). Resuming from the current second. "
        "(61 failed attempts in a row)" in caplog.text
    )
    line = "Metrics payload recovered after 61 failed attempts."
    assert caplog.text.count(line) == 1


@mocketize
def test_a_single_failed_dispatch_logs_no_recovery_line(caplog, session):
    stub_lease()
    ingest_statuses(500, 200)
    configure_web_only()

    assert len(attempts(session, range(1000, 1006))) == 5
    assert "recovered" not in caplog.text


@mocketize
def test_a_rejected_token_and_a_rejected_payload_do_not_lengthen_the_interval(session):
    stub_lease()
    ingest_statuses(401, 413, 401, 413, 401)
    configure_web_only()

    assert attempts(session, range(1000, 1005)) == [1000, 1001, 1002, 1003, 1004]


@mocketize
def test_a_process_without_the_lease_runs_one_dispatch_loop_one_lease_loop_and_no_sample_loop(
    monkeypatch,
):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.01)
    dispatcher = configure_workers_only()

    with patch.object(plan, "release_macros") as release:
        assert dispatcher.start()
        time.sleep(0.15)

        assert len(loop_threads("hirefire-dispatch")) == 1
        assert len(loop_threads("hirefire-lease")) == 1
        assert loop_threads("hirefire-sample") == []
        release.assert_not_called()
        dispatcher.stop()


@mocketize
def test_every_loop_is_a_named_daemon_thread(monkeypatch):
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.01)
    dispatcher = configure_workers_only()
    names = ["hirefire-dispatch", "hirefire-lease", "hirefire-sample"]

    assert dispatcher.start()
    wait_until(lambda: all(loop_threads(name) for name in names))

    assert all(thread.daemon for name in names for thread in loop_threads(name))
    dispatcher.stop()


@mocketize
def test_the_dispatch_loop_waits_a_tick_between_passes(monkeypatch):
    assert Session.TICK == 1
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.05)
    dispatcher = configure_web_only()
    passes = []

    with patch.object(CPU, "sample", side_effect=lambda: passes.append(True)):
        assert dispatcher.start()
        time.sleep(0.32)
        dispatcher.stop()

    assert 3 <= len(passes) <= 12


@mocketize
def test_the_sample_loop_waits_a_tick_between_rounds(monkeypatch):
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.05)
    dispatcher = configure_workers_only()
    checks = []

    with patch.object(
        Lease, "sample_if_due", side_effect=lambda _round: checks.append(True)
    ):
        assert dispatcher.start()
        time.sleep(0.32)
        dispatcher.stop()

    assert 2 <= len(checks) <= 12


def test_a_stop_without_a_flush_returns_while_the_dispatch_loop_is_in_a_request():
    in_request = threading.Event()
    release = threading.Event()

    def submit(_body):
        in_request.set()
        release.wait(5)
        return answer()

    dispatcher = configure_web_only()

    try:
        with ingest(submit):
            assert dispatcher.start()
            assert in_request.wait(2)

            assert seconds_to(lambda: dispatcher.stop(flush=False)) < 1
            assert loop_threads("hirefire-dispatch")
    finally:
        release.set()


def test_a_loop_that_finishes_a_pass_after_a_stop_does_not_wait_out_a_tick(monkeypatch):
    in_request = threading.Event()
    release = threading.Event()

    def submit(_body):
        in_request.set()
        release.wait(5)
        return answer()

    monkeypatch.setattr(Session, "TICK", 3)
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 5)

    with ingest(submit):
        assert dispatcher.start()
        assert in_request.wait(2)
        loop = loop_threads("hirefire-dispatch")[0]
        dispatcher.stop(flush=False)
        release.set()

        loop.join(1)
        assert not loop.is_alive(), "the dispatch loop waited out a tick after the stop"


@mocketize
def test_start_replaces_a_dispatch_loop_that_ended():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()

    with patch.object(Session, "_dispatch_loop", lambda session: None):
        assert dispatcher.start()
        wait_until(lambda: not loop_threads("hirefire-dispatch"))
    assert not dispatcher.running()

    assert dispatcher.start()
    assert dispatcher.running()
    assert len(loop_threads("hirefire-dispatch")) == 1
    dispatcher.stop()


@mocketize
def test_a_start_after_a_stop_runs_one_loop_of_each_kind():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_workers_only()

    for _ in range(3):
        assert dispatcher.start()
        wait_until(lambda: loop_threads("hirefire-lease"))
        assert dispatcher.stop()
    assert dispatcher.start()
    wait_until(lambda: len(loop_threads("hirefire-lease")) == 1)

    assert len(loop_threads("hirefire-dispatch")) == 1
    dispatcher.stop()
    wait_until(
        lambda: not loop_threads("hirefire-dispatch")
        and not loop_threads("hirefire-lease")
    )


@mocketize
def test_a_process_with_no_sampler_and_no_job_library_runs_no_lease_loop(monkeypatch):
    monkeypatch.setattr(plan, "any_library_loaded", lambda: False)
    monkeypatch.setattr(Session, "TICK", 0.01)
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()

    assert dispatcher.start()
    wait_until(lambda: len(bodies) > 0)
    time.sleep(0.05)

    assert loop_threads("hirefire-lease") == []
    dispatcher.stop()


@mocketize
def test_the_lease_loop_starts_once_a_sampler_is_registered_after_the_start(
    monkeypatch,
):
    monkeypatch.setattr(plan, "any_library_loaded", lambda: False)
    monkeypatch.setattr(Session, "TICK", 0.01)
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()

    assert dispatcher.start()
    HireFire.configuration.dyno("worker", lambda: 42)

    wait_until(lambda: loop_threads("hirefire-lease"))
    wait_until(lambda: lease_requests() > 0)
    dispatcher.stop()


@mocketize
def test_the_lease_loop_starts_once_a_job_library_is_loaded_after_the_start(
    monkeypatch,
):
    loaded = []
    monkeypatch.setattr(plan, "any_library_loaded", lambda: bool(loaded))
    monkeypatch.setattr(Session, "TICK", 0.01)
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()

    assert dispatcher.start()
    wait_until(lambda: ingest_requests() > 0)
    assert loop_threads("hirefire-lease") == []
    loaded.append("rq")

    wait_until(lambda: lease_requests() > 0)
    dispatcher.stop()


@mocketize
def test_the_macro_of_a_job_library_loaded_after_the_start_is_attached_on_a_later_pass(
    monkeypatch, load_macros
):
    attached = []
    monkeypatch.setattr(Session, "TICK", 0.01)
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    load_macros()
    dispatcher = configure_web_only()

    assert dispatcher.start()
    wait_until(lambda: ingest_requests() > 0)
    assert attached == []
    load_macros(celery=fake_macro(attach=lambda: attached.append("celery")))

    wait_until(lambda: attached)
    dispatcher.stop()


@mocketize
def test_a_lease_loop_that_could_not_start_is_started_on_a_later_pass(
    monkeypatch, caplog
):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.01)
    dispatcher = configure_workers_only()
    start = threading.Thread.start
    failed = []

    def fail_once(thread):
        if thread.name == "hirefire-lease" and not failed:
            failed.append(thread)
            raise RuntimeError("can't start new thread")
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_once)
    assert dispatcher.start()

    wait_until(lambda: loop_threads("hirefire-lease"))
    assert "[HireFire] RuntimeError: can't start new thread" in caplog.text
    dispatcher.stop()


@mocketize
def test_the_lease_loop_replaces_a_sample_loop_that_ended(monkeypatch):
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.01)
    dispatcher = configure_workers_only()
    held = [True]
    granted = Lease.granted
    monkeypatch.setattr(Lease, "granted", lambda lease: held[0] and granted(lease))

    with patch.object(plan, "release_macros") as release:
        assert dispatcher.start()
        wait_until(lambda: loop_threads("hirefire-sample"))
        first = loop_threads("hirefire-sample")[0]
        release.assert_not_called()
        held[0] = False
        wait_until(lambda: not first.is_alive())
        release.assert_called_once_with(HireFire.configuration.logger)
        held[0] = True

        wait_until(lambda: loop_threads("hirefire-sample"))
        assert loop_threads("hirefire-sample")[0] is not first
        dispatcher.stop()


@mocketize
def test_an_exception_of_any_class_in_a_pass_is_logged_and_the_loop_goes_on(
    monkeypatch, caplog
):
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.01)

    def worker():
        raise SystemExit("outside every except")

    HireFire.configuration.dyno("worker", worker)
    dispatcher = HireFire.configuration.dispatcher

    assert dispatcher.start()
    wait_until(lambda: "[HireFire] SystemExit: outside every except" in caplog.text)
    time.sleep(0.05)

    assert len(loop_threads("hirefire-sample")) == 1
    assert dispatcher.running()
    dispatcher.stop()


@mocketize
def test_the_lease_is_renewed_while_a_sample_round_is_still_running(monkeypatch):
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Lease-TTL": "5",
            "HireFire-Sample-Frequency": "15",
        },
        body=json.dumps({"job_queues": [{"name": "worker", "strategy": "jqs"}]}),
    )
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.01)
    entered = threading.Event()
    gate = threading.Event()

    def worker():
        entered.set()
        gate.wait(5)
        return 7

    HireFire.configuration.dyno("worker", worker)
    dispatcher = HireFire.configuration.dispatcher
    monotonic = time.monotonic
    ahead = [0]
    monkeypatch.setattr(time, "monotonic", lambda: monotonic() + ahead[0])

    try:
        assert dispatcher.start()
        assert entered.wait(2)
        ahead[0] = 6

        wait_until(lambda: lease_requests() >= 2)
        assert not gate.is_set()
    finally:
        gate.set()
        dispatcher.stop()


@mocketize
def test_stop_does_not_wait_for_a_sampler_that_hangs(caplog):
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    entered = threading.Event()
    gate = threading.Event()

    def worker():
        entered.set()
        gate.wait(5)
        return 0

    HireFire.configuration.dyno("worker", worker)
    dispatcher = HireFire.configuration.dispatcher

    try:
        assert dispatcher.start()
        assert entered.wait(2)

        assert seconds_to(dispatcher.stop) < 1
        assert not dispatcher.running()
        assert "final flush is skipped" not in caplog.text
    finally:
        gate.set()


def test_stop_skips_the_final_flush_when_the_dispatch_loop_is_still_in_a_request(
    monkeypatch, caplog
):
    assert Dispatcher.JOIN_TIMEOUT == 5
    entered = threading.Event()
    gate = threading.Event()
    requests = []

    def submit(body):
        requests.append(body)
        entered.set()
        gate.wait(5)
        return answer()

    monkeypatch.setattr(Dispatcher, "JOIN_TIMEOUT", 0.05)
    dispatcher = configure_web_only()

    try:
        with ingest(submit):
            assert dispatcher.start()
            assert entered.wait(2)

            assert seconds_to(dispatcher.stop) < 1
            assert (
                "The dispatch loop did not stop within 0.05 seconds. "
                "The final flush is skipped." in caplog.text
            )
            assert len(requests) == 1
            assert not dispatcher.running()
    finally:
        gate.set()


def test_a_stop_that_skipped_the_final_flush_leaves_no_connection_open_once_the_request_ends(
    monkeypatch,
):
    entered = threading.Event()
    gate = threading.Event()
    closed = []

    def submit(_body):
        entered.set()
        gate.wait(5)
        return answer()

    monkeypatch.setattr(Client, "close", lambda client: closed.append(client))
    monkeypatch.setattr(Dispatcher, "JOIN_TIMEOUT", 0.05)
    dispatcher = configure_web_only()

    try:
        with ingest(submit):
            assert dispatcher.start()
            assert entered.wait(2)
            assert dispatcher.stop()
            assert closed == []
            gate.set()

            wait_until(lambda: len(closed) == 1)
    finally:
        gate.set()


@mocketize
def test_a_new_session_does_not_sample_while_a_round_of_the_stopped_session_is_still_running(
    monkeypatch,
):
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.01)
    entered = queue.Queue()
    release = queue.Queue()
    rounds = []

    def worker():
        rounds.append(True)
        entered.put(True)
        release.get(timeout=5)
        return 1

    HireFire.configuration.dyno("worker", worker)
    dispatcher = HireFire.configuration.dispatcher

    try:
        assert dispatcher.start()
        entered.get(timeout=2)
        assert dispatcher.stop()
        assert dispatcher.start()
        time.sleep(0.2)
        assert len(rounds) == 1

        release.put(True)
        entered.get(timeout=2)
        assert len(rounds) == 2
        release.put(True)
        dispatcher.stop()
    finally:
        for _ in range(5):
            release.put(True)


@mocketize
def test_stop_without_flush_discards_the_buffer_and_posts_nothing_more():
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()

    with freeze_time(at(1000)):
        assert dispatcher.start()
        wait_until(lambda: len(bodies) == 1)
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        assert dispatcher.stop(flush=False)

    assert len(bodies) == 1
    assert HireFire.configuration.buffer.flush() == {}
    assert not dispatcher.running()


@mocketize
def test_stop_leaves_the_dispatcher_ready_to_start_again_when_the_final_flush_raises():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()

    assert dispatcher.start()
    with patch.object(Session, "flush", side_effect=RuntimeError("flush failed")):
        with pytest.raises(RuntimeError):
            dispatcher.stop()

    assert dispatcher.start()
    assert dispatcher.running()
    dispatcher.stop()


def test_a_start_while_a_stop_is_in_progress_does_nothing():
    dispatcher = configure_web_only()
    stopper = threading.current_thread()
    during_stop = []

    def submit(_body):
        if threading.current_thread() is stopper:
            during_stop.append(dispatcher.start())
        return answer()

    with ingest(submit):
        assert dispatcher.start()
        time.sleep(0.1)
        HireFire.configuration.buffer.sample("web", "rqt", 5)
        dispatcher.stop()

    assert during_stop == [False]
    assert not dispatcher.running()
    assert loop_threads("hirefire-dispatch") == []


@mocketize
def test_a_start_after_a_stop_in_the_same_process_keeps_what_was_buffered_in_between():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()
    assert dispatcher.start()
    dispatcher.stop()
    HireFire.configuration.buffer.sample("web", "rqt", 7)

    assert dispatcher.start()

    wait_until(lambda: sent(bodies, "web", "rqt", [7.0, 1]))
    dispatcher.stop()


@mocketize
def test_abandoning_inherited_state_stops_reporting_and_empties_the_buffer():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()

    with freeze_time(at(1000)):
        assert dispatcher.start()
        wait_until(lambda: len(bodies) == 1)
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        dispatcher.abandon_inherited_state()

    assert not dispatcher.running()
    assert not dispatcher.stop()
    assert HireFire.configuration.buffer.flush() == {}
    assert len(bodies) == 1
    assert dispatcher.start()
    dispatcher.stop()


def test_a_failure_while_abandoning_inherited_state_is_logged_and_does_not_raise(
    caplog,
):
    dispatcher = configure_web_only()

    with patch.object(
        HireFire.configuration.buffer,
        "reinit_after_fork",
        side_effect=RuntimeError("reinit boom"),
    ):
        dispatcher.abandon_inherited_state()

    line = "[HireFire] Could not abandon inherited dispatcher state: reinit boom"
    assert line in caplog.text


@mocketize
def test_a_web_process_that_has_not_served_a_request_sends_nothing_before_its_prefork_wait_ends(
    monkeypatch,
):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "PREFORK_WAIT", 0.3)
    dispatcher = configure_web_and_workers()

    assert dispatcher.start()
    time.sleep(0.1)
    assert request_paths() == []

    wait_until(lambda: ingest_requests() > 0)
    dispatcher.stop()


@mocketize
def test_a_web_process_stopped_within_its_prefork_wait_has_sent_nothing(monkeypatch):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "PREFORK_WAIT", 5)
    dispatcher = configure_web_and_workers()

    assert dispatcher.start()
    assert seconds_to(lambda: dispatcher.stop(flush=False)) < 1

    wait_until(lambda: not loop_threads("hirefire-dispatch"))
    assert request_paths() == []


@mocketize
def test_a_web_process_that_has_served_a_request_does_not_wait_before_its_first_pass(
    monkeypatch,
):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "PREFORK_WAIT", 5)
    dispatcher = configure_web_only()
    HireFire.configuration.mark_http_active()

    assert dispatcher.start()

    wait_until(lambda: ingest_requests() > 0)
    dispatcher.stop()


@mocketize
def test_a_process_that_is_not_a_web_process_does_not_wait_before_its_first_pass(
    monkeypatch,
):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "PREFORK_WAIT", 5)
    monkeypatch.setenv("DYNO", "worker.1")
    dispatcher = configure_workers_only()

    assert dispatcher.start()

    wait_until(lambda: lease_requests() > 0)
    dispatcher.stop()


def test_a_dispatcher_is_stalled_when_its_dispatch_loop_has_not_run_for_ten_seconds(
    monkeypatch,
):
    assert Session.STALL_LIMIT == 10
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    dispatcher = HireFire.configuration.dispatcher
    assert not dispatcher.stalled()

    with patch.object(Session, "_dispatch_loop", lambda session: None):
        assert dispatcher.start()

    clock[0] += Session.STALL_LIMIT
    assert not dispatcher.stalled()
    clock[0] += 0.1
    assert dispatcher.stalled()


@mocketize
def test_a_dispatcher_whose_dispatch_loop_runs_is_not_stalled(monkeypatch):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setattr(Session, "TICK", 0.01)
    monotonic = time.monotonic
    ahead = [0]
    monkeypatch.setattr(time, "monotonic", lambda: monotonic() + ahead[0])
    dispatcher = configure_workers_only()

    assert dispatcher.start()
    ahead[0] = Session.STALL_LIMIT + 1

    wait_until(lambda: not dispatcher.stalled())
    dispatcher.stop()
