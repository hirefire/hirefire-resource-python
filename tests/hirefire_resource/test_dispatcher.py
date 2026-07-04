import json
import logging
import os
import threading
import time
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from mocket import Mocket, mocketize
from mocket.mockhttp import Entry, Response

from hirefire_resource import HireFire
from hirefire_resource.client import RequestError
from hirefire_resource.cpu.usage import Usage
from hirefire_resource.dispatcher import Dispatcher
from tests.helpers import at, set_HIREFIRE_TOKEN  # noqa: F401

INGEST_URL = "https://data.hirefire.io/metrics/ingest"
LEASE_URL = "https://data.hirefire.io/metrics/lease"


@pytest.fixture(autouse=True)
def with_token(set_HIREFIRE_TOKEN):
    pass


def stub_lease(granted=False):
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": str(granted).lower(),
            "HireFire-Sample-Frequency": "15",
        },
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


def configure_web_and_workers():
    config = HireFire.configuration
    config.dyno("web")
    config.dyno("worker", lambda: 42)
    config.dyno("mailer", lambda: 18)
    return config.dispatcher


def configure_web_only():
    config = HireFire.configuration
    config.dyno("web")
    return config.dispatcher


def configure_workers_only():
    config = HireFire.configuration
    config.dyno("worker", lambda: 42)
    config.dyno("mailer", lambda: 18)
    return config.dispatcher


def configure_cpu_only(monkeypatch, name="clock"):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", name)
    config = HireFire.configuration
    config.dyno(name, tracking="cpu")
    return config.dispatcher


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
        HireFire.configuration.buffer.sample_web(12)
        HireFire.configuration.buffer.sample_web(8)
        dispatcher._tick()

    assert len(bodies) == 1
    assert bodies[0][0]["name"] == "web"
    assert list(bodies[0][0]["samples"].values())[0] == [12, 8]


@mocketize
def test_logs_the_payload_when_verbose_is_set(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("HIREFIRE_VERBOSE", "1")
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample_web(12)
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

    assert bodies[0][0]["samples"] == {"1000": []}


@mocketize
def test_backfills_seconds_skipped_between_dispatches():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1003)):
        dispatcher._tick()

    assert bodies[1][0]["samples"] == {"1001": [], "1002": [], "1003": []}


@mocketize
def test_backfill_preserves_buffered_samples():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1003)):
        HireFire.configuration.buffer.sample_web(5)
        dispatcher._tick()

    assert bodies[1][0]["samples"] == {"1001": [], "1002": [], "1003": [5]}


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

    assert sorted(bodies[2][0]["samples"].keys()) == [
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

    keys = [int(key) for key in bodies[1][0]["samples"].keys()]
    assert min(keys) == 1100 - Dispatcher.WEB_BACKFILL_LIMIT
    assert max(keys) == 1100
    assert len(keys) == Dispatcher.WEB_BACKFILL_LIMIT + 1


@mocketize
def test_lease_unauthorized_does_not_log_error(caplog):
    caplog.set_level(logging.ERROR)
    Entry.single_register(Entry.POST, LEASE_URL, status=401)
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._worker_tick()
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
        HireFire.configuration.buffer.sample_web(7)
        dispatcher._tick()

    assert HireFire.configuration.buffer.flush()["web"] == {}
    assert "Dispatch error" not in caplog.text


@mocketize
def test_web_buffer_repopulated_on_dispatch_failure():
    stub_lease()
    Entry.single_register(Entry.POST, INGEST_URL, status=500)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample_web(7)
        dispatcher._tick()

    assert HireFire.configuration.buffer.flush()["web"][1000] == [7]


@mocketize
def test_oversized_payload_is_dropped_without_a_request(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        for _ in range(15000):
            HireFire.configuration.buffer.sample_web(12345)
        dispatcher._tick()

    assert bodies == []
    assert HireFire.configuration.buffer.flush()["web"] == {}
    assert "Dropped metrics payload" in caplog.text


@mocketize
def test_an_oversized_payload_without_web_data_drops_without_touching_the_watermark(
    caplog,
):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    config = HireFire.configuration
    config.dyno("w" * 70_000, lambda: 1)
    dispatcher = config.dispatcher
    dispatcher._worker_tick()
    dispatcher._tick()

    assert bodies == []
    assert "Dropped metrics payload" in caplog.text
    assert dispatcher._last_web_second is None


@mocketize
def test_oversized_drop_advances_the_watermark_past_the_hole():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1010)):
        for _ in range(15000):
            HireFire.configuration.buffer.sample_web(12345)
        dispatcher._tick()
    with freeze_time(at(1012)):
        dispatcher._tick()

    assert len(bodies) == 2
    assert sorted(bodies[1][0]["samples"].keys()) == ["1011", "1012"]


@mocketize
def test_combined_web_and_worker_dispatch():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    with freeze_time(at(1000)):
        dispatcher = configure_web_and_workers()
        HireFire.configuration.buffer.sample_web(5)
        dispatcher._worker_tick()
        dispatcher._tick()

    entries = bodies[0]
    assert any(entry["name"] == "web" and "samples" in entry for entry in entries)
    assert any(
        entry["name"] == "worker" and entry.get("sample") == 42 for entry in entries
    )


@mocketize
def test_dispatch_tick_does_not_run_worker_sampling():
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
        HireFire.configuration.buffer.sample_web(5)
        dispatcher._tick()

    assert [entry["name"] for entry in bodies[0]] == ["web"]
    assert sampled == []


@mocketize
def test_worker_tick_samples_without_dispatching_and_a_later_tick_delivers_it():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("worker", lambda: 42)
        dispatcher = HireFire.configuration.dispatcher

        dispatcher._worker_tick()
        assert bodies == []

        dispatcher._tick()

    assert len(bodies) == 1
    assert any(
        entry["name"] == "worker" and entry.get("sample") == 42 for entry in bodies[0]
    )


@mocketize
def test_lease_granted_dispatches_workers():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._worker_tick()
    dispatcher._tick()

    assert any(
        entry["name"] == "worker" and entry.get("sample") == 42 for entry in bodies[0]
    )


@mocketize
def test_lease_denied_skips_worker_collection():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._worker_tick()
    dispatcher._tick()

    assert bodies == []


@mocketize
def test_dispatches_cpu_samples_in_the_samples_format(monkeypatch):
    bodies = capture_ingest_bodies()
    with patch.object(Usage, "available_cpus", return_value=1.0), patch.object(
        Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (0.5, "cgroup_v2")]
    ):
        dispatcher = configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            dispatcher._tick()
        with freeze_time(at(1001)):
            dispatcher._tick()

    assert len(bodies) == 1
    entry = bodies[0][0]
    assert entry["name"] == "clock"
    assert entry["samples"] == {"1001": [50.0]}


@mocketize
def test_cpu_first_tick_seeds_baseline_without_dispatching(monkeypatch):
    bodies = capture_ingest_bodies()
    with patch.object(Usage, "available_cpus", return_value=1.0), patch.object(
        Usage, "reading", return_value=(0.0, "cgroup_v2")
    ):
        dispatcher = configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            dispatcher._tick()

    assert bodies == []


@mocketize
def test_cpu_samples_are_not_repopulated_on_dispatch_failure(monkeypatch):
    Entry.single_register(Entry.POST, INGEST_URL, status=500)
    with patch.object(Usage, "available_cpus", return_value=1.0), patch.object(
        Usage, "reading", side_effect=[(0.0, "cgroup_v2"), (0.5, "cgroup_v2")]
    ):
        dispatcher = configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            dispatcher._tick()
        with freeze_time(at(1001)):
            dispatcher._tick()

    assert HireFire.configuration.buffer.flush()["cpu"] == {}


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
        HireFire.configuration.buffer.sample_web(12)
        dispatcher._tick()

    assert bodies[0][0]["samples"] == {"1000": [12]}


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

    assert bodies[0][0]["samples"] == {"1000": []}
    assert bodies[1][0]["samples"] == {"1001": [], "1002": []}


@mocketize
def test_unresolved_identity_keeps_heartbeat():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()

    with freeze_time(at(1000)):
        dispatcher._tick()

    assert bodies[0][0]["samples"] == {"1000": []}


@mocketize
def test_mismatched_cpu_collector_stays_dormant_through_the_tick(monkeypatch):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    HireFire.configuration.dyno("web")
    HireFire.configuration.dyno("worker", tracking="cpu")
    dispatcher = HireFire.configuration.dispatcher

    with freeze_time(at(1000)):
        dispatcher._tick()

    assert [entry["name"] for entry in bodies[0]] == ["web"]


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
            HireFire.configuration.buffer.sample_web(12)
            dispatcher._worker_tick()
            dispatcher._tick()

    assert len(bodies) == 1
    assert "Network error" in caplog.text


@mocketize
def test_tick_dispatches_when_a_sampler_raises(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    def boom():
        raise RuntimeError("Redis down")

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("web")
        HireFire.configuration.dyno("worker", boom)
        dispatcher = HireFire.configuration.dispatcher
        dispatcher._worker_tick()
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


@mocketize
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

    config.buffer.sample_web(5)
    dispatcher.start()

    try:
        deadline = time.time() + 5
        while not any(entry["name"] == "web" for body in bodies for entry in body):
            assert time.time() < deadline, "web metrics never dispatched"
            time.sleep(0.05)
    finally:
        release.set()
        dispatcher.stop()


@mocketize
def test_stop_flushes_the_buffer():
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    dispatcher._running = True
    dispatcher._pid = os.getpid()

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample_web(7)
        dispatcher.stop()

    assert len(bodies) == 1
    assert bodies[0][0]["samples"] == {"1000": [7]}


@mocketize
def test_stop_closes_the_persistent_connections():
    dispatcher = configure_workers_only()
    dispatcher._running = True
    dispatcher._pid = os.getpid()

    with patch.object(dispatcher._client, "close") as client_close, patch.object(
        dispatcher._lease, "close"
    ) as lease_close:
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
        HireFire.configuration.buffer.sample_web(7)
        with patch.object(
            dispatcher, "_build_payload", side_effect=RuntimeError("boom")
        ):
            dispatcher._tick()

    assert bodies == []
    assert "Dispatch error" in caplog.text
    assert HireFire.configuration.buffer.flush()["web"][1000] == [7]


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
def test_dispatch_pacing_uses_the_monotonic_clock_not_the_wall_clock():
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
    dispatcher._worker_tick()
    dispatcher._tick()

    assert HireFire.configuration.buffer.flush()["web"] == {}
    assert "Dispatch error" in caplog.text
