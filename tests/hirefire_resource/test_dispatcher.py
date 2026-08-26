import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from mocket import Mocket, mocketize
from mocket.mockhttp import Entry, Response

from hirefire_resource import HireFire, plan
from hirefire_resource.client import RequestError
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.source.cpu.usage import Usage
from tests.helpers import at, set_HIREFIRE_TOKEN  # noqa: F401

INGEST_URL = "https://data.hirefire.io/metrics/ingest"
LEASE_URL = "https://data.hirefire.io/metrics/lease"


@pytest.fixture(autouse=True)
def with_token(set_HIREFIRE_TOKEN):
    pass


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


class IngestBodies:
    def _items(self):
        return [
            json.loads(request.body)
            for request in Mocket.request_list()
            if request.path == "/metrics/ingest"
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
def test_dispatches_web_metrics():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 12)
        HireFire.configuration.buffer.sample("web", "rqt", 8)
        dispatcher._tick()

    assert len(bodies) == 1
    assert bodies[0][0]["name"] == "web"
    assert list(bodies[0][0]["metrics"]["rqt"].values())[0] == [10.0, 2]


@mocketize
def test_logs_the_payload_when_verbose_is_set(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("HIREFIRE_VERBOSE", "1")
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 12)
        dispatcher._tick()

    assert "Dispatching metrics" in caplog.text


@mocketize
def test_no_dispatch_when_nothing_configured():
    stub_lease()
    bodies = capture_ingest_bodies()

    HireFire.configuration.dispatcher._tick()

    assert bodies == []


@mocketize
def test_first_dispatch_claims_only_the_current_second():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()

    assert bodies[0][0]["metrics"]["rqt"] == {"1000": []}


@mocketize
def test_backfills_seconds_skipped_between_dispatches():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1003)):
        dispatcher._tick()

    assert bodies[1][0]["metrics"]["rqt"] == {"1001": [], "1002": [], "1003": []}


@mocketize
def test_backfill_preserves_buffered_samples():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1003)):
        HireFire.configuration.buffer.sample("web", "rqt", 5)
        dispatcher._tick()

    assert bodies[1][0]["metrics"]["rqt"] == {"1001": [], "1002": [], "1003": [5.0, 1]}


@mocketize
def test_seconds_from_a_failed_dispatch_are_reclaimed_by_the_next_success():
    stub_lease()
    Entry.register(
        Entry.POST,
        INGEST_URL,
        Response(status=200),
        Response(status=500),
        Response(status=200),
    )
    bodies = IngestBodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1003)):
        dispatcher._tick()
    with freeze_time(at(1005)):
        dispatcher._tick()

    assert sorted(bodies[2][0]["metrics"]["rqt"].keys()) == [
        "1001",
        "1002",
        "1003",
        "1004",
        "1005",
    ]


@mocketize
def test_backfill_is_capped_at_the_limit():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1100)):
        dispatcher._tick()

    keys = [int(key) for key in bodies[1][0]["metrics"]["rqt"].keys()]
    assert min(keys) == 1100 - Dispatcher.RQT_BACKFILL_LIMIT
    assert max(keys) == 1100
    assert len(keys) == Dispatcher.RQT_BACKFILL_LIMIT + 1


@mocketize
def test_lease_unauthorized_does_not_log_error(caplog):
    caplog.set_level(logging.ERROR)
    Entry.single_register(Entry.POST, LEASE_URL, status=401)
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._job_queue_tick()
    dispatcher._tick()

    assert bodies == []
    assert "401" not in caplog.text


@mocketize
def test_web_buffer_discarded_on_unauthorized(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=401)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        dispatcher._tick()

    assert "web" not in HireFire.configuration.buffer.flush()
    assert dispatcher._last_rqt_second == 1000
    assert "Dispatch error" not in caplog.text


@mocketize
def test_web_buffer_repopulated_on_dispatch_failure():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=500)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        dispatcher._tick()

    assert HireFire.configuration.buffer.flush()["web"]["rqt"][1000] == {
        "sum": 7.0,
        "count": 1,
    }


@mocketize
def test_oversized_payload_is_dropped_without_a_request(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        inject_oversized_series()
        dispatcher._tick()

    assert bodies == []
    assert "web" not in HireFire.configuration.buffer.flush()
    assert "Dropped metrics payload" in caplog.text


@mocketize
def test_an_oversized_payload_without_web_data_drops_without_touching_the_watermark(
    caplog,
):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=False)
    bodies = capture_ingest_bodies()

    dispatcher = HireFire.configuration.dispatcher
    inject_oversized_series(name="worker", strategy="jql")
    dispatcher._tick()

    assert bodies == []
    assert "Dropped metrics payload" in caplog.text
    assert dispatcher._last_rqt_second is None


@mocketize
def test_oversized_drop_advances_the_watermark_past_the_hole():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1010)):
        inject_oversized_series()
        dispatcher._tick()
    with freeze_time(at(1012)):
        dispatcher._tick()

    assert len(bodies) == 2
    assert sorted(bodies[1][0]["metrics"]["rqt"].keys()) == ["1011", "1012"]


@mocketize
def test_combined_web_and_worker_dispatch():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    with freeze_time(at(1000)):
        dispatcher = configure_web_and_workers()
        HireFire.configuration.buffer.sample("web", "rqt", 5)
        dispatcher._job_queue_tick()
        dispatcher._tick()

    entries = bodies[0]
    assert any(entry["name"] == "web" and "metrics" in entry for entry in entries)
    assert any(
        entry["name"] == "worker" and entry.get("metrics", {}).get("jql")
        for entry in entries
    )


@mocketize
def test_dispatch_tick_does_not_run_job_queue_sampling():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()
    sampled = []

    def sampler():
        sampled.append(True)
        return 42

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("web")
        HireFire.configuration.dyno("worker", sampler)
        dispatcher = HireFire.configuration.dispatcher
        HireFire.configuration.buffer.sample("web", "rqt", 5)
        dispatcher._tick()

    assert [entry["name"] for entry in bodies[0]] == ["web"]
    assert sampled == []


@mocketize
def test_job_queue_tick_samples_without_dispatching_and_a_later_tick_delivers_it():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("worker", lambda: 42)
        dispatcher = HireFire.configuration.dispatcher

        dispatcher._job_queue_tick()
        assert bodies == []

        dispatcher._tick()

    assert len(bodies) == 1
    assert any(
        entry["name"] == "worker" and entry.get("metrics", {}).get("jql")
        for entry in bodies[0]
    )


@mocketize
def test_lease_granted_dispatches_workers():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._job_queue_tick()
    dispatcher._tick()

    assert any(
        entry["name"] == "worker" and entry.get("metrics", {}).get("jql")
        for entry in bodies[0]
    )


@mocketize
def test_sample_trace_attached_when_grant_trace_true():
    stub_lease(granted=True, trace=True)
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._job_queue_tick()
    dispatcher._tick()

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
def test_oversized_sample_trace_is_stripped_so_metrics_still_ship(caplog):
    stub_lease()
    bodies = capture_ingest_bodies()
    caplog.set_level(logging.ERROR)
    dispatcher = configure_web_only()
    dispatcher._lease.trace = lambda: True
    dispatcher._pending_sample_trace = {
        "wave_ms": 1.0,
        "ops": [
            {
                "adapter": "rq",
                "strategy": "jqs",
                "queues": ["q" * 40_000],
                "options": {},
                "ms": 1.0,
            }
        ],
    }

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        dispatcher._tick()

    assert len(bodies) == 1
    assert "sample_trace" not in bodies[0][0]
    assert "rqt" in bodies[0][0]["metrics"]
    assert "Dropped metrics payload" not in caplog.text
    assert dispatcher._pending_sample_trace is None


@mocketize
def test_sample_trace_absent_without_grant_trace():
    stub_lease(granted=True, trace=False)
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._job_queue_tick()
    dispatcher._tick()

    assert bodies
    for entry in bodies[0]:
        assert "sample_trace" not in entry


@mocketize
def test_verbose_logs_sample_timings_without_server_trace(monkeypatch, caplog):
    monkeypatch.setenv("HIREFIRE_VERBOSE", "1")
    caplog.set_level(logging.INFO)
    stub_lease(granted=True, trace=False)

    dispatcher = configure_workers_only()
    dispatcher._job_queue_tick()

    assert "sample_job_queues wave_ms=" in caplog.text
    assert "sample adapter=" in caplog.text


@mocketize
def test_lease_denied_skips_worker_collection():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._job_queue_tick()
    dispatcher._tick()

    assert bodies == []


@mocketize
def test_dispatches_cpu_samples_in_the_nested_format(monkeypatch):
    bodies = capture_ingest_bodies()
    with (
        patch.object(Usage, "available_cpus", return_value=1.0),
        patch.object(
            Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (0.5, "cgroup_v2")]
        ),
    ):
        dispatcher = configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            dispatcher._tick()
        with freeze_time(at(1001)):
            dispatcher._tick()

    assert len(bodies) == 1
    entry = bodies[0][0]
    assert entry["name"] == "clock"
    assert entry["metrics"] == {"cpu": {"1001": 50.0}}


@mocketize
def test_dispatches_jqs_and_wrk_as_sibling_bare_numbers():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_workers_only()

    with freeze_time(at(2500)):
        HireFire.configuration.buffer.sample("worker", "jqs", 12)
        HireFire.configuration.buffer.sample("worker", "wrk", 3)
        dispatcher._dispatch()

    assert len(bodies) >= 1
    entry = next(e for e in bodies[-1] if e["name"] == "worker")
    jqs_leaf = entry["metrics"]["jqs"]["2500"]
    wrk_leaf = entry["metrics"]["wrk"]["2500"]
    assert jqs_leaf == 12
    assert wrk_leaf == 3
    assert isinstance(jqs_leaf, (int, float))
    assert isinstance(wrk_leaf, (int, float))
    assert not isinstance(
        wrk_leaf, list
    ), "wrk must be bare number like jqs, not rqt [v,n]"


@mocketize
def test_cpu_first_tick_seeds_baseline_without_dispatching(monkeypatch):
    bodies = capture_ingest_bodies()
    with (
        patch.object(Usage, "available_cpus", return_value=1.0),
        patch.object(Usage, "reading", return_value=(0.0, "cgroup_v2")),
    ):
        dispatcher = configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            dispatcher._tick()

    assert bodies == []


@mocketize
def test_cpu_samples_are_not_repopulated_on_dispatch_failure(monkeypatch):
    Entry.single_register(Entry.POST, INGEST_URL, status=500)
    with (
        patch.object(Usage, "available_cpus", return_value=1.0),
        patch.object(
            Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (0.5, "cgroup_v2")]
        ),
    ):
        dispatcher = configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            dispatcher._tick()
        with freeze_time(at(1001)):
            dispatcher._tick()

    data = HireFire.configuration.buffer.flush()
    assert "clock" not in data or "cpu" not in data.get("clock", {})


@mocketize
def test_non_web_process_does_not_heartbeat_the_web_name(monkeypatch):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "worker.1")
    HireFire.configuration.dyno("web")
    dispatcher = HireFire.configuration.dispatcher

    dispatcher._tick()

    assert bodies == []


@mocketize
def test_non_web_process_still_delivers_real_web_samples(monkeypatch):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "worker.1")
    HireFire.configuration.dyno("web")
    dispatcher = HireFire.configuration.dispatcher

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 12)
        dispatcher._tick()

    assert bodies[0][0]["metrics"]["rqt"] == {"1000": [12.0, 1]}


@mocketize
def test_matching_identity_keeps_heartbeat_and_backfill(monkeypatch):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "web.1")
    HireFire.configuration.dyno("web")
    dispatcher = HireFire.configuration.dispatcher

    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1002)):
        dispatcher._tick()

    assert bodies[0][0]["metrics"]["rqt"] == {"1000": []}
    assert bodies[1][0]["metrics"]["rqt"] == {"1001": [], "1002": []}


@mocketize
def test_unresolved_identity_does_not_synthesize_liveness():
    stub_lease()
    bodies = capture_ingest_bodies()

    config = HireFire.configuration
    config.dyno("web")
    dispatcher = config.dispatcher
    with freeze_time(at(1000)):
        dispatcher._tick()

    assert bodies == []


@mocketize
def test_always_on_cpu_uses_identity_name_through_the_tick(monkeypatch):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    dispatcher = HireFire.configuration.dispatcher

    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1001)):
        dispatcher._tick()

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
def test_tick_dispatches_when_the_lease_request_fails(caplog):
    caplog.set_level(logging.ERROR)
    bodies = capture_ingest_bodies()

    with freeze_time(at(1000)):
        dispatcher = configure_web_and_workers()
        with patch.object(
            dispatcher._lease,
            "request_if_due",
            side_effect=RequestError(
                "Network error (ConnectionRefusedError: refused)."
            ),
        ):
            HireFire.configuration.buffer.sample("web", "rqt", 12)
            dispatcher._job_queue_tick()
            dispatcher._tick()

    assert len(bodies) == 1
    assert "Network error" in caplog.text


@mocketize
def test_tick_dispatches_when_a_sampler_raises(caplog, monkeypatch):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "web.1")

    def boom():
        raise RuntimeError("Redis down")

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("worker", boom)
        dispatcher = HireFire.configuration.dispatcher
        dispatcher._job_queue_tick()
        dispatcher._tick()

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


def test_stale_loop_generation_stops_after_restart():
    dispatcher = configure_web_only()
    ticks = {"count": 0}
    original_tick = dispatcher._tick

    def counting_tick():
        ticks["count"] += 1
        original_tick()

    dispatcher._tick = counting_tick  # type: ignore[method-assign]
    dispatcher._running = True
    dispatcher._pid = os.getpid()
    generation = 1
    dispatcher._generation = generation

    assert dispatcher._loop_active(generation)

    dispatcher._running = False
    dispatcher._pid = None
    assert not dispatcher._loop_active(generation)

    dispatcher._generation = 2
    dispatcher._running = True
    dispatcher._pid = os.getpid()
    assert not dispatcher._loop_active(generation)
    assert dispatcher._loop_active(2)


def test_loop_until_stopped_logs_raising_tick_and_continues(caplog):
    caplog.set_level(logging.ERROR)
    dispatcher = Dispatcher()
    dispatcher._running = True
    dispatcher._pid = os.getpid()
    dispatcher._generation = 1
    calls = {"n": 0}

    def tick(_generation):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick boom")
        dispatcher._running = False

    with patch("hirefire_resource.dispatcher.time.sleep"):
        dispatcher._loop_until_stopped(1, tick)

    assert calls["n"] >= 2
    assert "tick boom" in caplog.text
    assert "RuntimeError" in caplog.text


@mocketize
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_a_hung_worker_sampler_does_not_stall_web_dispatch():
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    bodies = IngestBodies()

    release = threading.Event()

    def hung_sampler():
        release.wait()
        return 1

    config = HireFire.configuration
    config.dyno("web")
    config.dyno("worker", hung_sampler)
    dispatcher = config.dispatcher

    config.buffer.sample("web", "rqt", 5)
    dispatcher.start()

    try:
        deadline = time.time() + 5
        while not any(entry["name"] == "web" for body in bodies for entry in body):
            assert time.time() < deadline, "web metrics never dispatched"
            time.sleep(0.05)
    finally:
        release.set()
        dispatcher.stop()
        dispatcher._client.close()


@mocketize
def test_stop_flushes_the_buffer():
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    dispatcher._running = True
    dispatcher._pid = os.getpid()

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        dispatcher.stop()

    assert len(bodies) == 1
    assert bodies[0][0]["metrics"]["rqt"] == {"1000": [7.0, 1]}


@mocketize
def test_stop_closes_the_persistent_connections():
    dispatcher = configure_workers_only()
    dispatcher._running = True
    dispatcher._pid = os.getpid()

    with (
        patch.object(dispatcher._client, "close") as client_close,
        patch.object(dispatcher._lease, "close") as lease_close,
    ):
        dispatcher.stop()

    client_close.assert_called_once()
    lease_close.assert_called_once()


@mocketize
def test_web_only_dispatch_never_requests_a_lease():
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()

    assert "/metrics/lease" not in request_paths()


@mocketize
def test_tick_survives_a_payload_build_error(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        with patch.object(
            dispatcher, "_build_payload", side_effect=RuntimeError("boom")
        ):
            dispatcher._tick()

    assert bodies == []
    assert "Dispatch error" in caplog.text
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
def test_dispatch_frequency_defaults_to_one_without_the_header():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1001)):
        dispatcher._tick()

    assert len(bodies) == 2


@mocketize
def test_honors_a_server_supplied_dispatch_frequency():
    stub_lease()
    stub_ingest_with_dispatch_frequency(5)
    bodies = IngestBodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1002)):
        dispatcher._tick()
    with freeze_time(at(1004)):
        dispatcher._tick()
    with freeze_time(at(1005)):
        dispatcher._tick()

    assert len(bodies) == 2


@mocketize
def test_clamps_an_over_large_dispatch_frequency_to_the_maximum():
    stub_lease()
    stub_ingest_with_dispatch_frequency(Dispatcher.MAX_DISPATCH_FREQUENCY + 100)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()

    assert dispatcher._dispatch_frequency == Dispatcher.MAX_DISPATCH_FREQUENCY


@mocketize
def test_ignores_a_non_positive_dispatch_frequency():
    stub_lease()
    stub_ingest_with_dispatch_frequency(0)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()

    assert dispatcher._dispatch_frequency == Dispatcher.DEFAULT_DISPATCH_FREQUENCY


@mocketize
def test_ignores_an_unparseable_dispatch_frequency():
    stub_lease()
    stub_ingest_with_dispatch_frequency("nonsense")

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()

    assert dispatcher._dispatch_frequency == Dispatcher.DEFAULT_DISPATCH_FREQUENCY


@mocketize
def test_dispatch_pacing_follows_the_monotonic_clock_not_the_wall_clock():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    mono = [500.0]
    with freeze_time(at(1000)), patch("time.monotonic", side_effect=lambda: mono[0]):
        dispatcher._tick()
        mono[0] = 502.0
        dispatcher._tick()

    assert len(bodies) == 2


@mocketize
def test_dispatch_failure_without_web_data_does_not_repopulate(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=500)

    dispatcher = configure_workers_only()
    dispatcher._job_queue_tick()
    dispatcher._tick()

    assert "web" not in HireFire.configuration.buffer.flush()
    assert "Dispatch error" in caplog.text


@mocketize
def test_413_advances_watermark_without_repopulate(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease()
    Entry.single_register(
        Entry.POST, INGEST_URL, status=413, body=b'{"error":"payload too large"}'
    )

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 7)
        dispatcher._tick()

    assert "web" not in HireFire.configuration.buffer.flush()
    assert dispatcher._last_rqt_second == 1000
    assert "Dropped metrics payload" in caplog.text


@mocketize
def test_payload_size_limit_is_32768():
    assert Dispatcher.PAYLOAD_SIZE_LIMIT == 32_768


def test_healthy_running_snapshots_thread_ref():
    dispatcher = Dispatcher()
    dispatcher._running = True
    dispatcher._stopping = False
    dispatcher._pid = os.getpid()

    live = threading.Thread(target=time.sleep, args=(30,), daemon=True)
    live.start()

    class RacyDispatcher(Dispatcher):
        def __init__(self, inner, values):
            self._running = inner._running
            self._stopping = inner._stopping
            self._pid = inner._pid
            self._values = list(values)

        @property
        def _thread(self):
            if self._values:
                return self._values.pop(0)
            return None

    racy = RacyDispatcher(dispatcher, [live, None])
    assert racy._healthy_running() is True


def test_ensure_job_queue_loop_fast_path_snapshots_thread_ref():
    dispatcher = Dispatcher()
    dispatcher._running = True
    dispatcher._stopping = False
    dispatcher._pid = os.getpid()

    live = threading.Thread(target=time.sleep, args=(30,), daemon=True)
    live.start()

    class RacyDispatcher(Dispatcher):
        def __init__(self, inner, values):
            self._running = inner._running
            self._stopping = inner._stopping
            self._pid = inner._pid
            self._mutex = threading.Lock()
            self._generation = 0
            self._values = list(values)

        @property
        def _job_queue_thread(self):
            if self._values:
                return self._values.pop(0)
            return None

        def _enter_race(self):
            raise AssertionError("fast path should return before enter_race")

    racy = RacyDispatcher(dispatcher, [live, None])
    racy.ensure_job_queue_loop()


@mocketize
def test_encode_omits_non_finite_rqt_mean(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()

    with freeze_time(at(1000)):
        buffer = HireFire.configuration.buffer
        with buffer._mutex:
            buffer._metrics["web"] = {
                "rqt": {
                    1000: {"sum": float("inf"), "count": 1},
                    999: {"sum": 10.0, "count": 1},
                }
            }
        dispatcher._tick()

    assert len(bodies) >= 1
    rqt = bodies[0][0]["metrics"]["rqt"]
    assert "1000" not in rqt
    assert rqt["999"] == [10.0, 1]
    assert "Omitting rqt second" in caplog.text


@mocketize
def test_dispatch_with_stale_generation_does_not_post():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 10)
    dispatcher._running = False
    dispatcher._stopping = False
    dispatcher._pid = os.getpid()
    dispatcher._generation = 2

    dispatcher._dispatch(1)

    assert len(bodies) == 0
    data = HireFire.configuration.buffer.flush()
    assert "web" in data and "rqt" in data["web"]


@mocketize
def test_dispatch_dead_gen_after_flush_does_not_repopulate_when_not_final_flush():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 10)
    dispatcher._running = True
    dispatcher._stopping = False
    dispatcher._stopping_flush = False
    dispatcher._pid = os.getpid()
    dispatcher._generation = 1

    calls = {"n": 0}
    original = dispatcher._loop_active

    def racy(generation):
        calls["n"] += 1
        return calls["n"] == 1

    dispatcher._loop_active = racy
    dispatcher._dispatch(1)

    assert len(bodies) == 0
    assert HireFire.configuration.buffer.flush() == {}
    dispatcher._loop_active = original


@mocketize
def test_dispatch_dead_gen_after_flush_handoffs_for_final_flush():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 10)
    dispatcher._running = False
    dispatcher._stopping = True
    dispatcher._stopping_flush = True
    dispatcher._pid = os.getpid()
    dispatcher._generation = 1

    calls = {"n": 0}

    def racy(_generation):
        calls["n"] += 1
        return calls["n"] == 1

    dispatcher._loop_active = racy
    dispatcher._dispatch(1)

    assert len(bodies) == 0
    data = HireFire.configuration.buffer.flush()
    assert "web" in data and "rqt" in data["web"]


@mocketize
def test_dispatch_dead_gen_after_successful_post_skips_watermark_and_frequency():
    Entry.single_register(
        Entry.POST,
        INGEST_URL,
        status=200,
        headers={"HireFire-Dispatch-Frequency": "10"},
    )
    stub_lease()
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 10)
    dispatcher._running = True
    dispatcher._stopping = False
    dispatcher._stopping_flush = False
    dispatcher._pid = os.getpid()
    dispatcher._generation = 1
    dispatcher._last_rqt_second = 999
    dispatcher._dispatch_frequency = 1

    calls = {"n": 0}

    def racy(_generation):
        calls["n"] += 1
        return calls["n"] <= 2

    dispatcher._loop_active = racy
    dispatcher._dispatch(1)

    assert dispatcher._last_rqt_second == 999
    assert dispatcher._dispatch_frequency == 1


@mocketize
def test_dispatch_dead_gen_on_error_does_not_repopulate_without_handoff():
    Entry.single_register(Entry.POST, INGEST_URL, status=500)
    stub_lease()
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 10)
    dispatcher._running = True
    dispatcher._stopping = False
    dispatcher._stopping_flush = False
    dispatcher._pid = os.getpid()
    dispatcher._generation = 1

    calls = {"n": 0}

    def racy(_generation):
        calls["n"] += 1
        return calls["n"] <= 2

    dispatcher._loop_active = racy

    def boom(_body):
        raise ConnectionRefusedError("down")

    with patch.object(dispatcher._client, "submit_samples", side_effect=boom):
        dispatcher._dispatch(1)

    assert HireFire.configuration.buffer.flush() == {}


@mocketize
def test_dispatch_dead_gen_on_error_handoffs_for_final_flush():
    stub_lease()
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 10)
    dispatcher._running = False
    dispatcher._stopping = True
    dispatcher._stopping_flush = True
    dispatcher._pid = os.getpid()
    dispatcher._generation = 1

    calls = {"n": 0}

    def racy(_generation):
        calls["n"] += 1
        return calls["n"] <= 2

    dispatcher._loop_active = racy

    def boom(_body):
        raise ConnectionRefusedError("down")

    with patch.object(dispatcher._client, "submit_samples", side_effect=boom):
        dispatcher._dispatch(1)

    data = HireFire.configuration.buffer.flush()
    assert "web" in data and "rqt" in data["web"]


@mocketize
def test_stop_without_flush_skips_final_dispatch():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()
    assert dispatcher.start()
    HireFire.configuration.buffer.sample("web", "rqt", 42)
    assert dispatcher.stop(flush=False)
    assert HireFire.configuration.buffer.flush() == {}
    assert dispatcher.running() is False


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
def test_dispatch_if_due_does_not_advance_pacing_on_dead_gen():
    stub_lease()
    capture_ingest_bodies()
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 10)
    dispatcher._running = True
    dispatcher._stopping = False
    dispatcher._stopping_flush = False
    dispatcher._pid = os.getpid()
    dispatcher._generation = 1
    dispatcher._next_dispatch_at = None

    calls = {"n": 0}

    def racy(_generation):
        calls["n"] += 1
        return calls["n"] == 1

    dispatcher._loop_active = racy
    dispatcher._dispatch_if_due(1)

    assert dispatcher._next_dispatch_at is None


@mocketize
def test_stale_generation_cannot_dispatch_after_restart():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()
    assert dispatcher.start()
    gen = dispatcher._generation
    HireFire.configuration.buffer.sample("web", "rqt", 5)
    dispatcher.stop()
    assert dispatcher.start()
    assert dispatcher._generation != gen
    dispatcher._dispatch(gen)
    assert dispatcher.running()
    dispatcher.stop()


@mocketize
def test_ensure_job_queue_loop_starts_when_enter_race_becomes_true():
    stub_lease()
    capture_ingest_bodies()
    with patch(
        "hirefire_resource.plan.any_allowlisted_job_queue_library_loaded",
        return_value=False,
    ):
        dispatcher = configure_web_only()
        assert dispatcher.start()
        assert dispatcher._job_queue_thread is None
        HireFire.configuration.dyno("worker", lambda: 1)
        dispatcher.ensure_job_queue_loop()
        assert dispatcher._job_queue_thread is not None
        assert dispatcher._job_queue_thread.is_alive()
        dispatcher.stop()


@mocketize
def test_ensure_job_queue_loop_restarts_dead_job_queue_thread():
    stub_lease()
    capture_ingest_bodies()
    dispatcher = configure_web_and_workers()
    assert dispatcher.start()
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    dispatcher._job_queue_thread = dead
    dispatcher.ensure_job_queue_loop()
    assert dispatcher._job_queue_thread is not dead
    assert dispatcher._job_queue_thread.is_alive()
    dispatcher.stop()


@mocketize
def test_ensure_job_queue_loop_is_noop_when_stopping():
    stub_lease()
    capture_ingest_bodies()
    dispatcher = configure_web_and_workers()
    assert dispatcher.start()
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    dispatcher._stopping = True
    dispatcher._job_queue_thread = dead
    dispatcher.ensure_job_queue_loop()
    assert dispatcher._job_queue_thread is dead
    dispatcher._stopping = False
    dispatcher.stop()


@mocketize
def test_ensure_job_queue_loop_is_noop_when_not_running():
    dispatcher = configure_web_only()
    dispatcher.ensure_job_queue_loop()
    assert dispatcher._job_queue_thread is None


@mocketize
def test_ensure_job_queue_loop_is_noop_without_enter_race():
    stub_lease()
    capture_ingest_bodies()
    with patch(
        "hirefire_resource.plan.any_allowlisted_job_queue_library_loaded",
        return_value=False,
    ):
        dispatcher = configure_web_only()
        assert dispatcher.start()
        assert dispatcher._job_queue_thread is None
        dispatcher.ensure_job_queue_loop()
        assert dispatcher._job_queue_thread is None
        dispatcher.stop()


@mocketize
def test_ensure_job_queue_loop_logs_when_thread_spawn_fails(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease()
    dispatcher = configure_workers_only()
    dispatcher._running = True
    dispatcher._stopping = False
    dispatcher._pid = os.getpid()
    dispatcher._generation = 1
    with patch("threading.Thread.start", side_effect=RuntimeError("cannot spawn")):
        dispatcher.ensure_job_queue_loop()
    assert "Could not start job-queue loop" in caplog.text
    dispatcher._job_queue_thread = None
    dispatcher._running = False


@mocketize
def test_start_restarts_when_main_loop_thread_is_dead():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    dispatcher._running = True
    dispatcher._stopping = False
    dispatcher._pid = os.getpid()
    dispatcher._thread = dead
    dispatcher._generation = 1
    assert not dispatcher.running()
    assert dispatcher.start()
    restarted = dispatcher._thread
    assert restarted is not dead
    assert restarted.is_alive()
    assert dispatcher.running()
    dispatcher.stop()


@mocketize
def test_start_rejected_while_stopping():
    stub_lease()
    capture_ingest_bodies()
    dispatcher = configure_web_only()
    assert dispatcher.start()
    dispatcher._stopping = True
    assert dispatcher.start() is False
    dispatcher._stopping = False
    dispatcher.stop()


@mocketize
def test_stop_closes_transports_even_when_final_dispatch_raises():
    stub_lease()
    capture_ingest_bodies()
    dispatcher = configure_web_only()
    dispatcher._running = True
    dispatcher._pid = os.getpid()
    dispatcher._thread = None
    dispatcher._job_queue_thread = None

    with patch.object(
        dispatcher, "_dispatch", side_effect=RuntimeError("flush failed")
    ):
        with patch.object(dispatcher._client, "close") as client_close:
            with patch.object(dispatcher._lease, "demote") as demote:
                with patch.object(dispatcher._lease, "close") as lease_close:
                    with pytest.raises(RuntimeError):
                        dispatcher.stop()
                    client_close.assert_called()
                    demote.assert_called()
                    lease_close.assert_called()
    assert dispatcher._stopping is False


@mocketize
def test_stop_after_abandon_does_not_post_buffered_samples():
    stub_lease()
    bodies = capture_ingest_bodies()
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
def test_plan_adapter_overrides_local_sampler():
    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return True

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_latency(*queues, **options):
            return 9.9

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
    HireFire.configuration.dyno("worker", lambda: 1)
    dispatcher = HireFire.configuration.dispatcher

    with (
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan.supports_strategy", return_value=True),
        patch("hirefire_resource.plan._load_macro", return_value=Macro),
    ):
        dispatcher._job_queue_tick()
        dispatcher._tick()

    entry = next(e for e in bodies[0] if e["name"] == "worker")
    assert list(entry["metrics"]["jql"].values())[0] == 9.9


@mocketize
def test_unknown_plan_adapter_skips_without_local_fallback(caplog):
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
    dispatcher = HireFire.configuration.dispatcher
    dispatcher._job_queue_tick()
    dispatcher._tick()
    assert len(bodies) == 0
    assert "Unknown plan adapter" in caplog.text


@mocketize
def test_known_unloaded_adapter_skips_without_local_fallback(caplog):
    caplog.set_level(logging.ERROR)
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jql",
                "adapter": "celery",
                "queues": [],
                "options": {},
            }
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()
    HireFire.configuration.dyno("worker", lambda: 42)
    dispatcher = HireFire.configuration.dispatcher
    with (
        patch("hirefire_resource.plan.executable", return_value=False),
        patch("hirefire_resource.plan.known_adapter", return_value=True),
    ):
        dispatcher._job_queue_tick()
        dispatcher._job_queue_tick()
        dispatcher._tick()
    assert len(bodies) == 0
    assert caplog.text.count("is not loaded in this process") == 1


@mocketize
def test_executable_plan_without_local_dyno_holds_lease_and_samples():
    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return True

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_latency(*queues, **options):
            return 4.2

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
    dispatcher = HireFire.configuration.dispatcher
    with (
        patch(
            "hirefire_resource.plan.any_allowlisted_job_queue_library_loaded",
            return_value=True,
        ),
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan.supports_strategy", return_value=True),
        patch("hirefire_resource.plan._load_macro", return_value=Macro),
    ):
        assert dispatcher._enter_race()
        assert not HireFire.configuration.job_queues.any()
        dispatcher._job_queue_tick()
        assert dispatcher._lease.granted()
        dispatcher._tick()

    entry = next(e for e in bodies[0] if e["name"] == "worker")
    assert list(entry["metrics"]["jql"].values())[0] == 4.2


@mocketize
def test_plan_override_warns_once(caplog):
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 99)
    dispatcher = HireFire.configuration.dispatcher
    dispatcher._lease.job_queues = [
        {
            "name": "worker",
            "adapter": "celery",
            "strategy": "jql",
            "queues": [],
            "options": {},
        }
    ]
    caplog.set_level(logging.WARNING)
    with (
        patch.object(plan, "executable", return_value=True),
        patch.object(plan, "supports_strategy", return_value=True),
        patch.object(plan, "execute", return_value=None),
    ):
        dispatcher._sample_job_queues()
        dispatcher._sample_job_queues()

    assert caplog.text.count("UI adapter is configured") == 1
    assert "config.dyno" in caplog.text
    assert "You can remove" in caplog.text


@mocketize
def test_strategy_only_plan_uses_local_sampler(caplog):
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
    dispatcher = HireFire.configuration.dispatcher
    dispatcher._job_queue_tick()
    dispatcher._tick()

    entry = next(e for e in bodies[0] if e["name"] == "worker")
    assert list(entry["metrics"]["jqs"].values())[0] == 7
    assert "UI adapter is configured" not in caplog.text


@mocketize
def test_strategy_only_plan_reports_lease_name_not_local_dyno_spelling():
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
    dispatcher = HireFire.configuration.dispatcher
    dispatcher._job_queue_tick()
    dispatcher._tick()

    names = [e["name"] for e in bodies[0]]
    assert "worker" in names
    assert "Worker" not in names


@mocketize
def test_empty_string_adapter_uses_local_strategy_sampler():
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
    dispatcher = HireFire.configuration.dispatcher
    dispatcher._job_queue_tick()
    dispatcher._tick()

    entry = next(e for e in bodies[0] if e["name"] == "worker")
    assert list(entry["metrics"]["jqs"].values())[0] == 11


@mocketize
def test_empty_plan_with_local_samplers_still_holds_lease():
    stub_lease(granted=True, plan={"version": 1, "job_queues": []})
    HireFire.configuration.dyno("worker", lambda: 5)
    dispatcher = HireFire.configuration.dispatcher
    dispatcher._job_queue_tick()
    assert dispatcher._lease.granted()


@mocketize
def test_hold_lease_false_when_queue_required_entry_has_no_queues():
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jqs",
                "adapter": "celery",
                "queues": [],
                "options": {},
            }
        ],
    }
    stub_lease(granted=True, plan=plan)
    dispatcher = HireFire.configuration.dispatcher
    with (
        patch(
            "hirefire_resource.plan.any_allowlisted_job_queue_library_loaded",
            return_value=True,
        ),
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan.supports_strategy", return_value=True),
        patch(
            "hirefire_resource.plan.queues_required",
            side_effect=lambda adapter: str(adapter) == "celery",
        ),
    ):
        assert dispatcher._enter_race()
        assert not dispatcher._hold_lease(
            [
                {
                    "name": "worker",
                    "strategy": "jqs",
                    "adapter": "celery",
                    "queues": [],
                }
            ]
        )
        dispatcher._job_queue_tick()
        assert not dispatcher._lease.granted()


@mocketize
def test_mixed_plan_skips_empty_queues_required_entry_without_invoking_adapter():
    executed = []
    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jqs",
                "adapter": "rq",
                "queues": [],
                "options": {},
            },
            {
                "name": "mail",
                "strategy": "jqs",
                "adapter": "celery",
                "queues": [],
                "options": {},
            },
        ],
    }
    stub_lease(granted=True, plan=plan)
    dispatcher = HireFire.configuration.dispatcher

    def execute(entry, live=None):
        executed.append(entry.get("adapter"))

    with (
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan.supports_strategy", return_value=True),
        patch(
            "hirefire_resource.plan.queues_required",
            side_effect=lambda adapter: str(adapter) == "celery",
        ),
        patch("hirefire_resource.plan.execute", side_effect=execute),
    ):
        mixed = [
            {
                "name": "worker",
                "strategy": "jqs",
                "adapter": "rq",
                "queues": [],
            },
            {
                "name": "mail",
                "strategy": "jqs",
                "adapter": "celery",
                "queues": [],
            },
        ]
        assert dispatcher._hold_lease(mixed)
        dispatcher._job_queue_tick()

    assert executed == ["rq"]


@mocketize
def test_hold_lease_true_when_enumerating_adapter_has_empty_queue_list():
    dispatcher = HireFire.configuration.dispatcher
    with (
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan.supports_strategy", return_value=True),
        patch("hirefire_resource.plan.queues_required", return_value=False),
    ):
        assert dispatcher._hold_lease(
            [
                {
                    "name": "worker",
                    "strategy": "jqs",
                    "adapter": "rq",
                    "queues": [],
                }
            ]
        )


@mocketize
def test_hold_lease_false_when_only_unsupported_strategy_entries():
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
    dispatcher = HireFire.configuration.dispatcher
    with (
        patch(
            "hirefire_resource.plan.any_allowlisted_job_queue_library_loaded",
            return_value=True,
        ),
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan.supports_strategy", return_value=False),
    ):
        assert dispatcher._enter_race()
        dispatcher._job_queue_tick()
        assert not dispatcher._lease.granted()


@mocketize
def test_hold_lease_true_when_only_supported_plan_entries_without_local_dynos():
    dispatcher = HireFire.configuration.dispatcher
    with (
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan.supports_strategy", return_value=True),
    ):
        assert dispatcher._hold_lease(
            [
                {
                    "name": "worker",
                    "strategy": "jql",
                    "adapter": "celery",
                    "queues": ["default"],
                }
            ]
        )


@mocketize
def test_always_lease_non_renew_when_no_workers_and_no_executable_plan():
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
    dispatcher = HireFire.configuration.dispatcher
    with patch(
        "hirefire_resource.plan.any_allowlisted_job_queue_library_loaded",
        return_value=True,
    ):
        assert dispatcher._enter_race()
        dispatcher._job_queue_tick()
        assert not dispatcher._lease.granted()


@mocketize
def test_hold_demotion_logs_and_web_dispatch_continues(monkeypatch):
    logs = []

    class CaptureLogger:
        def info(self, message):
            logs.append(message)

        def error(self, message):
            logs.append(message)

        def warning(self, message):
            logs.append(message)

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
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    HireFire.configuration.logger = CaptureLogger()
    HireFire.configuration.mark_http_active()
    dispatcher = HireFire.configuration.dispatcher
    dispatcher._lease._expires_at = 0
    HireFire.configuration.buffer.sample("web", "rqt", 3)
    dispatcher._job_queue_tick()
    dispatcher._tick()
    assert any("Lease grant dropped" in m for m in logs)
    assert len(bodies) >= 1


@mocketize
def test_unsupported_plan_strategy_logs_once_and_skips_macro(caplog):
    caplog.set_level(logging.ERROR)
    calls = {"n": 0}

    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return strategy != "jql"

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_size(*queues, **options):
            return 1

        @staticmethod
        def job_queue_latency(*queues, **options):
            calls["n"] += 1
            raise AssertionError("should not be called")

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
    HireFire.configuration.dyno("other", lambda: 0)
    dispatcher = HireFire.configuration.dispatcher
    with (
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan.supports_strategy", return_value=False),
        patch("hirefire_resource.plan._load_macro", return_value=Macro),
    ):
        dispatcher._job_queue_tick()
        dispatcher._job_queue_tick()
        dispatcher._tick()
    assert calls["n"] == 0
    assert caplog.text.count("does not support") == 1
    assert not any(e["name"] == "worker" for body in bodies for e in body)


@mocketize
def test_partial_plan_holds_and_samples_only_executable_entries(caplog):
    caplog.set_level(logging.ERROR)

    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return True

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_latency(*queues, **options):
            return 2.5

    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jql",
                "adapter": "celery",
                "queues": ["default"],
            },
            {
                "name": "mailer",
                "strategy": "jql",
                "adapter": "rq",
                "queues": ["mail"],
            },
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()
    dispatcher = HireFire.configuration.dispatcher

    def executable(adapter):
        return adapter == "celery"

    def known(adapter):
        return adapter in ("celery", "rq")

    with (
        patch(
            "hirefire_resource.plan.any_allowlisted_job_queue_library_loaded",
            return_value=True,
        ),
        patch("hirefire_resource.plan.executable", side_effect=executable),
        patch("hirefire_resource.plan.known_adapter", side_effect=known),
        patch("hirefire_resource.plan.supports_strategy", return_value=True),
        patch("hirefire_resource.plan._load_macro", return_value=Macro),
    ):
        dispatcher._job_queue_tick()
        assert dispatcher._lease.granted()
        dispatcher._job_queue_tick()
        dispatcher._tick()

    names = {e["name"] for e in bodies[0]}
    assert "worker" in names
    assert "mailer" not in names
    assert caplog.text.count("is not loaded in this process") == 1


@mocketize
def test_partial_plan_unsupported_jql_and_supported_jqs_holds_and_samples_size():
    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return strategy == "jqs"

        @staticmethod
        def plan_options(strategy, options):
            return {}

        @staticmethod
        def plan_connection_options():
            return {}

        @staticmethod
        def job_queue_size(*queues, **options):
            return 7

        @staticmethod
        def job_queue_latency(*queues, **options):
            raise AssertionError("jql must not run")

    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jql",
                "adapter": "celery",
                "queues": ["default"],
            },
            {
                "name": "worker",
                "strategy": "jqs",
                "adapter": "celery",
                "queues": ["default"],
            },
        ],
    }
    stub_lease(granted=True, plan=plan)
    bodies = capture_ingest_bodies()
    dispatcher = HireFire.configuration.dispatcher

    def supports(adapter, strategy):
        return strategy == "jqs"

    with (
        patch(
            "hirefire_resource.plan.any_allowlisted_job_queue_library_loaded",
            return_value=True,
        ),
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan.supports_strategy", side_effect=supports),
        patch("hirefire_resource.plan._load_macro", return_value=Macro),
    ):
        dispatcher._job_queue_tick()
        assert dispatcher._lease.granted()
        dispatcher._tick()

    entry = next(e for e in bodies[0] if e["name"] == "worker")
    assert "jqs" in entry["metrics"]
    assert "jql" not in entry["metrics"]


@mocketize
def test_payload_size_limit_is_32768_with_strict_greater_drop(caplog):
    caplog.set_level(logging.ERROR)
    limit = Dispatcher.PAYLOAD_SIZE_LIMIT
    assert limit == 32_768
    stub_lease()
    posts = {"n": 0}
    dispatcher = configure_web_only()

    def submit(body):
        posts["n"] += 1
        return None

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample("web", "rqt", 1)
        with patch("json.dumps", return_value="e" * limit):
            with patch.object(dispatcher._client, "submit_samples", side_effect=submit):
                dispatcher._tick()
    assert posts["n"] == 1
    assert "Dropped metrics payload" not in caplog.text

    with freeze_time(at(1001)):
        HireFire.configuration.buffer.sample("web", "rqt", 1)
        with patch("json.dumps", return_value="o" * (limit + 1)):
            with patch.object(dispatcher._client, "submit_samples", side_effect=submit):
                dispatcher._tick()
    assert posts["n"] == 1
    assert "Dropped metrics payload" in caplog.text


@mocketize
def test_encode_clamps_rqt_sample_count_to_limit():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()
    limit = Dispatcher.SAMPLE_COUNT_LIMIT
    with freeze_time(at(1000)):
        buffer = HireFire.configuration.buffer
        with buffer._mutex:
            buffer._metrics["web"] = {
                "rqt": {1000: {"sum": 20.0 * (limit + 50), "count": limit + 50}}
            }
        dispatcher._tick()
    assert bodies[0][0]["metrics"]["rqt"]["1000"] == [20.0, limit]


@mocketize
def test_encode_omits_invalid_non_rqt_values():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()
    limit = Dispatcher.METRIC_VALUE_LIMIT
    dispatcher = configure_web_only()
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
                    996: "nope",
                    995: 4.5,
                },
                "cpu": {1000: -0.1, 999: 12.0},
            }
            buffer._metrics["web"] = {"rqt": {1000: {"sum": 1.0, "count": 1}}}
        dispatcher._tick()
    assert len(bodies) >= 1
    worker = next(e for e in bodies[0] if e["name"] == "worker")
    jql = worker["metrics"].get("jql", {})
    cpu = worker["metrics"].get("cpu", {})
    assert "1000" not in jql
    assert "999" not in jql
    assert "998" not in jql
    assert "997" not in jql
    assert "996" not in jql
    assert jql.get("995") == 4.5
    assert "1000" not in cpu
    assert cpu.get("999") == 12.0


@mocketize
def test_stop_returns_within_join_timeout_when_job_sampler_hangs(caplog):
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    caplog.set_level(logging.WARNING)

    gate = threading.Event()

    def hung_sampler():
        gate.wait()
        return 1

    HireFire.configuration.dyno("worker", hung_sampler)
    dispatcher = HireFire.configuration.dispatcher
    assert dispatcher.start()
    time.sleep(0.2)

    started = time.monotonic()
    assert dispatcher.stop()
    elapsed = time.monotonic() - started

    assert elapsed < Dispatcher.JOIN_TIMEOUT + 2
    assert "Abandoning thread" in caplog.text
    gate.set()


@mocketize
def test_fork_resets_dispatch_pacing_and_watermark():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    assert dispatcher.start()
    dispatcher._next_dispatch_at = 1_000_000.0
    dispatcher._last_rqt_second = 1_700_000_000

    child_pid = dispatcher._pid + 1
    with patch("os.getpid", return_value=child_pid):
        with (
            patch.object(dispatcher, "_tick"),
            patch.object(dispatcher, "_job_queue_tick"),
        ):
            assert dispatcher.start()
            assert dispatcher._next_dispatch_at is None
            assert dispatcher._last_rqt_second is None
            dispatcher.stop()


@mocketize
def test_fork_resets_always_on_cpu_and_warn_maps(monkeypatch):
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    monkeypatch.setenv("DYNO", "web.1")

    dispatcher = configure_web_only()
    first_cpu = HireFire.configuration.active_cpu_sources()[0]
    assert first_cpu is not None

    dispatcher._unloaded_adapter_warned = {"worker": True}
    dispatcher._plan_override_warned = {"worker": True}
    dispatcher._unknown_adapter_warned = {"worker": True}
    dispatcher._unsupported_strategy_warned = {"worker\0bunny\0jql": True}
    assert dispatcher.start()

    child_pid = dispatcher._pid + 1
    with patch("os.getpid", return_value=child_pid):
        assert dispatcher.start()
        second_cpu = HireFire.configuration.active_cpu_sources()[0]
        assert second_cpu is not first_cpu
        assert dispatcher._unloaded_adapter_warned == {}
        assert dispatcher._plan_override_warned == {}
        assert dispatcher._unknown_adapter_warned == {}
        assert dispatcher._unsupported_strategy_warned == {}
        dispatcher.stop()


@mocketize
def test_forked_child_start_reinitializes_buffer_mutex():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    assert dispatcher.start()
    buffer = HireFire.configuration.buffer
    buffer.sample("web", "rqt", 7)
    old_mutex = buffer._mutex

    child_pid = os.getpid() + 1
    with patch("os.getpid", return_value=child_pid):
        assert dispatcher.start()
        assert buffer._mutex is not old_mutex
        assert buffer.flush() == {}
        dispatcher.stop()


@mocketize
def test_jql_not_repopulated_on_dispatch_failure(caplog):
    stub_lease(granted=True)
    Entry.single_register(Entry.POST, INGEST_URL, status=500)
    caplog.set_level(logging.ERROR)

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("worker", lambda: 3)
        dispatcher = HireFire.configuration.dispatcher
        dispatcher._job_queue_tick()
        dispatcher._tick()

        assert HireFire.configuration.buffer.flush() == {}
        assert "Dispatch error" in caplog.text


@mocketize
def test_nested_payload_merges_rqt_and_cpu_under_one_name(monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    bodies = capture_ingest_bodies()
    with (
        patch.object(Usage, "available_cpus", return_value=1.0),
        patch.object(
            Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (0.5, "cgroup_v2")]
        ),
    ):
        HireFire.configuration.dyno("web")
        dispatcher = HireFire.configuration.dispatcher

        with freeze_time(at(1000)):
            dispatcher._tick()
        with freeze_time(at(1001)):
            HireFire.configuration.buffer.sample("web", "rqt", 12)
            dispatcher._tick()

    entry = next(e for e in bodies[-1] if e["name"] == "web")
    assert "rqt" in entry["metrics"]
    assert "cpu" in entry["metrics"]


@mocketize
def test_wire_payload_nested_multi_strategy_shape(monkeypatch):
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "web.1")

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("web")
        HireFire.configuration.dyno("worker", lambda: 3)
        dispatcher = HireFire.configuration.dispatcher

        HireFire.configuration.buffer.sample("web", "rqt", 12)
        HireFire.configuration.buffer.sample("web", "cpu", 25.0)
        dispatcher._job_queue_tick()
        dispatcher._tick()

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
def test_sample_job_queues_runs_plan_inside_around_job_queue_sample():
    order: list[str] = []
    executed: list[str] = []

    @contextmanager
    def fake_around():
        order.append("around-enter")
        yield
        order.append("around-exit")

    def fake_execute(entry, live=None):
        order.append("execute")
        executed.append(entry["adapter"])
        assert entry["strategy"] in ("jql", "jqs")

    dispatcher = configure_web_only()
    dispatcher._lease.job_queues = [
        {
            "name": "worker",
            "adapter": "celery",
            "strategy": "jql",
            "queues": ["default"],
        },
        {
            "name": "mailer",
            "adapter": "rq",
            "strategy": "jqs",
            "queues": ["mail"],
        },
    ]

    with (
        patch.object(plan, "around_job_queue_sample", fake_around),
        patch.object(plan, "execute", side_effect=fake_execute),
        patch.object(plan, "executable", return_value=True),
        patch.object(plan, "supports_strategy", return_value=True),
        patch.object(plan, "known_adapter", return_value=True),
    ):
        dispatcher._sample_job_queues()

    assert order == ["around-enter", "execute", "execute", "around-exit"]
    assert executed == ["celery", "rq"]


@mocketize
def test_first_start_does_not_clear_pre_start_rqt():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()
    HireFire.configuration.buffer.sample("web", "rqt", 5)
    with (
        patch.object(dispatcher, "_tick"),
        patch.object(dispatcher, "_job_queue_tick"),
    ):
        assert dispatcher.start()
    flushed = HireFire.configuration.buffer.flush()
    assert "web" in flushed and "rqt" in flushed["web"]
    dispatcher.stop()


@mocketize
def test_start_after_parent_stop_reinitializes_inherited_state():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    dispatcher = configure_web_only()
    assert dispatcher.start()
    first_cpu = HireFire.configuration.active_cpu_sources()[0]
    buffer = HireFire.configuration.buffer
    old_mutex = buffer._mutex

    dispatcher.stop(flush=False)
    parent_pid = dispatcher._pid
    assert parent_pid is not None

    with (
        patch("os.getpid", return_value=parent_pid + 1),
        patch.object(dispatcher, "_tick"),
        patch.object(dispatcher, "_job_queue_tick"),
    ):
        assert dispatcher.start()
        assert buffer._mutex is not old_mutex
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
        reinit.assert_called_once_with()


@mocketize
def test_start_after_fork_calls_reinit_macros_after_fork():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    assert dispatcher.start()
    child_pid = dispatcher._pid + 1

    try:
        with (
            patch("os.getpid", return_value=child_pid),
            patch.object(plan, "reinit_macros_after_fork") as reinit,
            patch.object(dispatcher, "_tick"),
            patch.object(dispatcher, "_job_queue_tick"),
        ):
            assert dispatcher.start()
            reinit.assert_called_once_with()
    finally:
        dispatcher.stop()


def test_encode_leaf_omits_non_numeric_non_rqt_silently(caplog):
    dispatcher = Dispatcher()
    with caplog.at_level(logging.ERROR):
        assert dispatcher._encode_leaf("jqs", "10") is None
        assert dispatcher._encode_leaf("jql", None) is None
    assert "Omitting" not in caplog.text


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
def test_unsupported_strategy_once_log_is_isolated_per_name_adapter_strategy(caplog):
    class Macro:
        @staticmethod
        def supports_plan_strategy(strategy):
            return False

        @staticmethod
        def job_queue_size(*_a, **_k):
            return 1

    plan = {
        "version": 1,
        "job_queues": [
            {
                "name": "worker",
                "strategy": "jql",
                "adapter": "celery",
                "queues": ["default"],
                "options": {},
            },
            {
                "name": "mailer",
                "strategy": "jql",
                "adapter": "celery",
                "queues": ["default"],
                "options": {},
            },
            {
                "name": "worker",
                "strategy": "jql",
                "adapter": "dramatiq",
                "queues": ["default"],
                "options": {},
            },
        ],
    }
    stub_lease(granted=True, plan=plan)
    dispatcher = HireFire.configuration.dispatcher
    HireFire.configuration.dyno("other", lambda: 0)
    with (
        caplog.at_level(logging.ERROR),
        patch("hirefire_resource.plan.executable", return_value=True),
        patch("hirefire_resource.plan._load_macro", return_value=Macro),
        patch("hirefire_resource.plan.supports_strategy", return_value=False),
    ):
        dispatcher._job_queue_tick()
        dispatcher._job_queue_tick()

    assert caplog.text.count("does not support") == 3
    warned = dispatcher._unsupported_strategy_warned
    assert "worker\0celery\0jql" in warned
    assert "mailer\0celery\0jql" in warned
    assert "worker\0dramatiq\0jql" in warned
