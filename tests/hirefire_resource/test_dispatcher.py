import json
import logging
import os
import threading
from unittest.mock import patch

import httpretty
import pytest
from freezegun import freeze_time

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
    httpretty.register_uri(
        httpretty.POST,
        LEASE_URL,
        status=200,
        adding_headers={
            "HireFire-Lease-Granted": str(granted).lower(),
            "HireFire-Sample-Frequency": "15",
        },
    )


def capture_ingest_bodies(status=200):
    bodies = []

    def callback(request, uri, response_headers):
        bodies.append(json.loads(request.body))
        return [status, response_headers, ""]

    httpretty.register_uri(httpretty.POST, INGEST_URL, body=callback)
    return bodies


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
    return [request.path for request in httpretty.latest_requests()]


@httpretty.activate
def test_starts_and_stops():
    stub_lease()
    httpretty.register_uri(httpretty.POST, INGEST_URL, status=200)

    dispatcher = configure_web_and_workers()

    assert not dispatcher.running()
    assert dispatcher.start()
    assert dispatcher.running()
    assert not dispatcher.start()  # idempotent
    assert dispatcher.stop()
    assert not dispatcher.running()
    assert not dispatcher.stop()  # idempotent


@httpretty.activate
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


@httpretty.activate
def test_no_dispatch_when_nothing_configured():
    stub_lease()
    bodies = capture_ingest_bodies()

    HireFire.configuration.dispatcher._tick()

    assert bodies == []


@httpretty.activate
def test_first_dispatch_claims_only_the_current_second():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()

    assert bodies[0][0]["samples"] == {"1000": []}


@httpretty.activate
def test_backfills_seconds_skipped_between_dispatches():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()
    with freeze_time(at(1003)):
        dispatcher._tick()

    assert bodies[1][0]["samples"] == {"1001": [], "1002": [], "1003": []}


@httpretty.activate
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


@httpretty.activate
def test_seconds_from_a_failed_dispatch_are_reclaimed_by_the_next_success():
    stub_lease()
    bodies = []
    calls = [0]

    def callback(request, uri, response_headers):
        calls[0] += 1
        bodies.append(json.loads(request.body))
        return [500 if calls[0] == 2 else 200, response_headers, ""]

    httpretty.register_uri(httpretty.POST, INGEST_URL, body=callback)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()  # 200 — watermark 1000
    with freeze_time(at(1003)):
        dispatcher._tick()  # 500 — watermark holds
    with freeze_time(at(1005)):
        dispatcher._tick()  # 200 — reclaims 1001..1005

    assert sorted(bodies[2][0]["samples"].keys()) == [
        "1001",
        "1002",
        "1003",
        "1004",
        "1005",
    ]


@httpretty.activate
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


@httpretty.activate
def test_lease_unauthorized_does_not_log_error(caplog):
    caplog.set_level(logging.ERROR)
    httpretty.register_uri(httpretty.POST, LEASE_URL, status=401)
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._tick()

    assert bodies == []
    assert "401" not in caplog.text


@httpretty.activate
def test_web_buffer_discarded_on_unauthorized(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease()
    httpretty.register_uri(httpretty.POST, INGEST_URL, status=401)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample_web(7)
        dispatcher._tick()

    assert HireFire.configuration.buffer.flush()["web"] == {}
    assert "Dispatch error" not in caplog.text


@httpretty.activate
def test_web_buffer_repopulated_on_dispatch_failure():
    stub_lease()
    httpretty.register_uri(httpretty.POST, INGEST_URL, status=500)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample_web(7)
        dispatcher._tick()

    assert HireFire.configuration.buffer.flush()["web"][1000] == [7]


@httpretty.activate
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


@httpretty.activate
def test_oversized_drop_advances_the_watermark_past_the_hole():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()  # watermark 1000
    with freeze_time(at(1010)):
        for _ in range(15000):
            HireFire.configuration.buffer.sample_web(12345)
        dispatcher._tick()  # oversized — dropped, watermark advances to 1010
    with freeze_time(at(1012)):
        dispatcher._tick()

    assert len(bodies) == 2
    assert sorted(bodies[1][0]["samples"].keys()) == ["1011", "1012"]


@httpretty.activate
def test_combined_web_and_worker_dispatch():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    with freeze_time(at(1000)):
        dispatcher = configure_web_and_workers()
        HireFire.configuration.buffer.sample_web(5)
        dispatcher._tick()

    entries = bodies[0]
    assert any(entry["name"] == "web" and "samples" in entry for entry in entries)
    assert any(
        entry["name"] == "worker" and entry.get("sample") == 42 for entry in entries
    )


@httpretty.activate
def test_lease_granted_dispatches_workers():
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._tick()

    assert any(
        entry["name"] == "worker" and entry.get("sample") == 42 for entry in bodies[0]
    )


@httpretty.activate
def test_lease_denied_skips_worker_collection():
    stub_lease()
    bodies = capture_ingest_bodies()

    dispatcher = configure_workers_only()
    dispatcher._tick()

    assert bodies == []


@httpretty.activate
def test_dispatches_cpu_samples_in_the_samples_format(monkeypatch):
    bodies = capture_ingest_bodies()
    with patch.object(Usage, "available_cpus", return_value=1.0), patch.object(
        Usage, "total_seconds", side_effect=[0.0, 0.5]
    ):
        dispatcher = configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            dispatcher._tick()  # seeds baseline only
        with freeze_time(at(1001)):
            dispatcher._tick()  # 0.5 core over 1s => 50%

    assert len(bodies) == 1
    entry = bodies[0][0]
    assert entry["name"] == "clock"
    assert entry["samples"] == {"1001": [50.0]}


@httpretty.activate
def test_cpu_first_tick_seeds_baseline_without_dispatching(monkeypatch):
    bodies = capture_ingest_bodies()
    with patch.object(Usage, "available_cpus", return_value=1.0), patch.object(
        Usage, "total_seconds", return_value=0.0
    ):
        dispatcher = configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            dispatcher._tick()

    assert bodies == []


@httpretty.activate
def test_cpu_samples_are_not_repopulated_on_dispatch_failure(monkeypatch):
    httpretty.register_uri(httpretty.POST, INGEST_URL, status=500)
    with patch.object(Usage, "available_cpus", return_value=1.0), patch.object(
        Usage, "total_seconds", side_effect=[0.0, 0.5]
    ):
        dispatcher = configure_cpu_only(monkeypatch, "clock")
        with freeze_time(at(1000)):
            dispatcher._tick()
        with freeze_time(at(1001)):
            dispatcher._tick()  # 500 — sample dropped, not re-buffered

    assert HireFire.configuration.buffer.flush()["cpu"] == {}


@httpretty.activate
def test_non_web_process_does_not_heartbeat_the_web_name(monkeypatch):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("DYNO", "worker.1")
    HireFire.configuration.dyno("web")
    dispatcher = HireFire.configuration.dispatcher

    dispatcher._tick()

    assert bodies == []


@httpretty.activate
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


@httpretty.activate
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


@httpretty.activate
def test_unresolved_identity_keeps_heartbeat():
    stub_lease()
    bodies = capture_ingest_bodies()
    dispatcher = configure_web_only()

    with freeze_time(at(1000)):
        dispatcher._tick()

    assert bodies[0][0]["samples"] == {"1000": []}


@httpretty.activate
def test_mismatched_cpu_collector_stays_dormant_through_the_tick(monkeypatch):
    stub_lease()
    bodies = capture_ingest_bodies()
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    HireFire.configuration.dyno("web")
    HireFire.configuration.dyno(
        "worker", tracking="cpu"
    )  # dormant here: identity is "web"
    dispatcher = HireFire.configuration.dispatcher

    with freeze_time(at(1000)):
        dispatcher._tick()

    assert [entry["name"] for entry in bodies[0]] == ["web"]


@httpretty.activate
def test_forked_child_restarts_the_dispatcher():
    stub_lease()
    httpretty.register_uri(httpretty.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    assert dispatcher.start()

    # Simulate a fork: _running is inherited from the parent, but its thread is not.
    child_pid = os.getpid() + 1
    with patch("os.getpid", return_value=child_pid):
        assert not dispatcher.running()
        assert dispatcher.start()
        assert dispatcher.running()
        dispatcher.stop()


@httpretty.activate
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
            dispatcher._tick()

    assert len(bodies) == 1
    assert "Network error" in caplog.text


@httpretty.activate
def test_tick_dispatches_when_a_sampler_raises(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=True)
    bodies = capture_ingest_bodies()

    def boom():
        raise RuntimeError("Redis down")

    with freeze_time(at(1000)):
        HireFire.configuration.dyno("web")
        HireFire.configuration.dyno("worker", boom)
        HireFire.configuration.dispatcher._tick()

    assert len(bodies) == 1
    assert [entry["name"] for entry in bodies[0]] == ["web"]
    assert "Redis down" in caplog.text


@httpretty.activate
def test_started_thread_dispatches_until_stopped():
    dispatched = threading.Event()

    def callback(request, uri, response_headers):
        dispatched.set()
        return [200, response_headers, ""]

    httpretty.register_uri(httpretty.POST, INGEST_URL, body=callback)

    dispatcher = configure_web_only()
    dispatcher.start()
    assert dispatched.wait(timeout=5)
    assert dispatcher.running()

    dispatcher.stop()
    assert not dispatcher.running()


@httpretty.activate
def test_stop_flushes_the_buffer():
    bodies = capture_ingest_bodies()

    dispatcher = configure_web_only()
    # Mark running without spawning the thread, so the only dispatch is stop's.
    dispatcher._running = True
    dispatcher._pid = os.getpid()

    with freeze_time(at(1000)):
        HireFire.configuration.buffer.sample_web(7)
        dispatcher.stop()

    assert len(bodies) == 1
    assert bodies[0][0]["samples"] == {"1000": [7]}


@httpretty.activate
def test_web_only_dispatch_never_requests_a_lease():
    httpretty.register_uri(httpretty.POST, INGEST_URL, status=200)

    dispatcher = configure_web_only()
    with freeze_time(at(1000)):
        dispatcher._tick()

    assert "/metrics/lease" not in request_paths()


@httpretty.activate
def test_dispatch_failure_without_web_data_does_not_repopulate(caplog):
    caplog.set_level(logging.ERROR)
    stub_lease(granted=True)
    httpretty.register_uri(httpretty.POST, INGEST_URL, status=500)

    dispatcher = configure_workers_only()
    dispatcher._tick()  # 500 — workers-only, so web data is empty

    assert HireFire.configuration.buffer.flush()["web"] == {}
    assert "Dispatch error" in caplog.text
