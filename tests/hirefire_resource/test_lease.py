import json
import logging
import os
import re
from unittest.mock import patch

import pytest
from freezegun import freeze_time
from mocket import Mocket, mocketize
from mocket.mockhttp import Entry, Response

from hirefire_resource.client import RequestError
from hirefire_resource.lease import Lease
from tests.helpers import at, set_HIREFIRE_TOKEN  # noqa: F401

LEASE_URL = "https://data.hirefire.io/metrics/lease"


@pytest.fixture(autouse=True)
def with_token(set_HIREFIRE_TOKEN):
    pass


def stub_lease(granted="false", **headers):
    adding_headers = {"HireFire-Lease-Granted": granted}
    adding_headers.update(headers)
    Entry.single_register(Entry.POST, LEASE_URL, status=200, headers=adding_headers)


def test_process_id_is_stable_uuid():
    lease = Lease()
    assert re.match(
        r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
        lease.process_id,
    )
    assert lease.process_id == lease.process_id


def test_not_granted_by_default():
    assert not Lease().granted()


@mocketize
def test_granted_after_successful_poll():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.granted()


@mocketize
def test_denied_after_poll():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert not lease.granted()


@mocketize
def test_updates_sample_frequency_from_response():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "30"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.sample_frequency == 30


@mocketize
def test_updates_ttl_from_response():
    stub_lease(granted="false", **{"HireFire-Lease-TTL": "30"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease._ttl == 30


@mocketize
def test_not_polled_before_interval_elapsed():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    lease.request_if_due(hold=lambda _plan: True)
    assert len(Mocket.request_list()) == 1


@mocketize
def test_silently_denied_on_unauthorized():
    Entry.single_register(Entry.POST, LEASE_URL, status=401)
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert not lease.granted()


@mocketize
def test_revokes_granted_lease_on_unauthorized():
    Entry.register(
        Entry.POST,
        LEASE_URL,
        Response(
            body="",
            status=200,
            headers={
                "HireFire-Lease-Granted": "true",
                "HireFire-Sample-Frequency": "15",
            },
        ),
        Response(body="", status=401),
    )

    with freeze_time(at(1000)) as frozen:
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)
        assert lease.granted()

        frozen.move_to(at(1015))
        lease.request_if_due(hold=lambda _plan: True)
        assert not lease.granted()


def test_transport_failure_demotes_and_waits_a_full_ttl():
    with patch(
        "http.client.HTTPSConnection.request", side_effect=ConnectionRefusedError
    ) as mock_request:
        lease = Lease()
        with pytest.raises(RequestError):
            lease.request_if_due(hold=lambda _plan: True)
        assert not lease.granted()

        lease.request_if_due(hold=lambda _plan: True)
        assert mock_request.call_count == 1


@mocketize
def test_transport_failure_revokes_granted_lease():
    stub_lease(granted="true")

    with freeze_time(at(1000)) as frozen:
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)
        assert lease.granted()

        with patch(
            "http.client.HTTPSConnection.request", side_effect=ConnectionRefusedError
        ):
            frozen.move_to(at(1015))
            with pytest.raises(RequestError):
                lease.request_if_due(hold=lambda _plan: True)
            assert not lease.granted()


@mocketize
def test_ttl_update_applies_to_the_current_window():
    stub_lease(granted="true", **{"HireFire-Lease-TTL": "30"})

    with freeze_time(at(1000)):
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)

    assert lease._expires_at == 1030


@mocketize
def test_raises_on_server_error():
    Entry.single_register(Entry.POST, LEASE_URL, status=500)
    lease = Lease()

    with pytest.raises(RequestError) as exc_info:
        lease.request_if_due(hold=lambda _plan: True)

    assert "Lease request failed" in str(exc_info.value)
    assert not lease.granted()


@mocketize
def test_sends_process_id_header():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert Mocket.last_request().headers.get("hirefire-process-id") == lease.process_id


@mocketize
def test_hold_false_drops_grant_without_sampling():
    body = json.dumps(
        {
            "version": 1,
            "job_queues": [{"name": "x", "strategy": "jql", "adapter": "nope"}],
        }
    )
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
            "HireFire-Lease-TTL": "30",
        },
        body=body,
    )
    lease = Lease()
    original = lease.process_id
    epoch_before = lease._epoch
    lease.request_if_due(hold=lambda _plan: False)
    assert not lease.granted()
    assert lease.job_queues == []
    assert lease.process_id != original
    assert lease._epoch == epoch_before


@mocketize
def test_sample_frequency_decrease_pulls_next_sample_forward():
    Entry.register(
        Entry.POST,
        LEASE_URL,
        Response(
            body=json.dumps({"version": 1, "job_queues": []}),
            status=200,
            headers={
                "HireFire-Lease-Granted": "true",
                "HireFire-Sample-Frequency": "15",
                "HireFire-Lease-TTL": "60",
            },
        ),
        Response(
            body=json.dumps({"version": 1, "job_queues": []}),
            status=200,
            headers={
                "HireFire-Lease-Granted": "true",
                "HireFire-Sample-Frequency": "1",
                "HireFire-Lease-TTL": "60",
            },
        ),
    )

    mono = [5000.0]
    with patch("time.monotonic", side_effect=lambda: mono[0]):
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)
        assert lease.granted()
        lease.sample_if_due(lambda: None)
        far_deadline = lease._next_sample_at

        lease._expires_at = mono[0] - 1
        lease.request_if_due(hold=lambda _plan: True)

        assert lease.sample_frequency == 1
        sooner = lease._next_sample_at
        assert sooner < far_deadline


def test_demote_clears_grant_and_invalidates_inflight_epoch():
    lease = Lease()
    lease._granted = True
    lease.job_queues = [{"name": "worker", "strategy": "jql"}]
    original = lease.process_id
    epoch = lease._epoch
    lease.demote()
    assert not lease.granted()
    assert lease.job_queues == []
    assert lease.process_id == original
    assert lease._epoch == epoch + 1


@mocketize
def test_demote_during_inflight_request_discards_late_grant():
    lease = Lease()
    client = lease._client

    class FakeResponse:
        status = 200
        headers = {
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "30",
            "HireFire-Lease-TTL": "120",
        }
        body = json.dumps(
            {
                "version": 1,
                "job_queues": [{"name": "worker", "strategy": "jql"}],
            }
        )

    def late_grant(_process_id):
        lease.demote()
        return FakeResponse()

    with patch.object(client, "request_lease", side_effect=late_grant):
        lease.request_if_due(hold=lambda _plan: True)

    assert not lease.granted()
    assert lease.job_queues == []
    assert lease.sample_frequency == 15


@mocketize
def test_regrant_rearms_next_sample_immediately():
    Entry.register(
        Entry.POST,
        LEASE_URL,
        Response(
            body=json.dumps({"version": 1, "job_queues": []}),
            status=200,
            headers={
                "HireFire-Lease-Granted": "true",
                "HireFire-Sample-Frequency": "60",
                "HireFire-Lease-TTL": "15",
            },
        ),
        Response(
            body="",
            status=200,
            headers={
                "HireFire-Lease-Granted": "false",
                "HireFire-Sample-Frequency": "60",
                "HireFire-Lease-TTL": "15",
            },
        ),
        Response(
            body=json.dumps({"version": 1, "job_queues": []}),
            status=200,
            headers={
                "HireFire-Lease-Granted": "true",
                "HireFire-Sample-Frequency": "60",
                "HireFire-Lease-TTL": "15",
            },
        ),
    )

    mono = [1000.0]
    with patch("time.monotonic", side_effect=lambda: mono[0]):
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)
        assert lease.granted()
        lease.sample_if_due(lambda: None)
        far = lease._next_sample_at
        assert far > mono[0] + 30

        mono[0] = 1015.0
        lease._expires_at = mono[0] - 1
        lease.request_if_due(hold=lambda _plan: True)
        assert not lease.granted()

        mono[0] = 1030.0
        lease._expires_at = mono[0] - 1
        lease.request_if_due(hold=lambda _plan: True)
        assert lease.granted()

        rearmed = lease._next_sample_at
        assert rearmed <= mono[0] + 1
        assert rearmed < far


@mocketize
def test_parses_grant_trace_true():
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body=json.dumps(
            {
                "version": 1,
                "trace": True,
                "job_queues": [{"name": "worker", "strategy": "jql"}],
            }
        ),
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.granted()
    assert lease.trace() is True


@mocketize
def test_trace_false_for_string_or_missing():
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body=json.dumps(
            {
                "version": 1,
                "trace": "true",
                "job_queues": [],
            }
        ),
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.granted()
    assert lease.trace() is False


@mocketize
def test_ignores_oversized_grant_body(caplog):
    caplog.set_level(logging.ERROR)
    oversized = "x" * (Lease.MAX_BODY_BYTES + 1)
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body=oversized,
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.granted()
    assert lease.job_queues == []
    assert "exceeded" in caplog.text


@mocketize
def test_truncates_plan_to_max_job_queues(caplog):
    caplog.set_level(logging.ERROR)
    entries = [
        {
            "name": f"w{i}",
            "strategy": "jql",
            "adapter": None,
            "queues": [],
            "options": {},
        }
        for i in range(Lease.MAX_JOB_QUEUES + 3)
    ]
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body=json.dumps({"version": 1, "job_queues": entries}),
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert len(lease.job_queues) == Lease.MAX_JOB_QUEUES
    assert "truncated" in caplog.text


@mocketize
def test_skips_invalid_plan_entries(caplog):
    caplog.set_level(logging.ERROR)
    long_name = "a" * (Lease.MAX_NAME_BYTES + 1)
    body = json.dumps(
        {
            "version": 1,
            "job_queues": [
                "not-a-hash",
                {"name": "", "strategy": "jql"},
                {"name": "ok", "strategy": ""},
                {"name": long_name, "strategy": "jql"},
                {
                    "name": "worker",
                    "strategy": "jql",
                    "adapter": None,
                    "queues": [],
                    "options": {},
                },
            ],
        }
    )
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body=body,
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert len(lease.job_queues) == 1
    assert lease.job_queues[0]["name"] == "worker"
    assert "skipped" in caplog.text


@mocketize
def test_invalid_json_grant_body_is_ignored(caplog):
    caplog.set_level(logging.ERROR)
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body="{not-json",
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.granted()
    assert lease.job_queues == []
    assert "not valid JSON" in caplog.text


@mocketize
def test_sample_if_due_yields_when_granted_and_due():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == [True]


@mocketize
def test_sample_if_due_skips_when_not_granted():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == []


@mocketize
def test_sample_if_due_skips_when_not_yet_due():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    lease.sample_if_due(lambda: None)

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == []


@mocketize
def test_failed_sample_consumes_its_window():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)

    def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        lease.sample_if_due(boom)

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == []


@mocketize
def test_sample_if_due_advances_next_sample_at():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "10"})

    with freeze_time(at(1000)):
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)
        lease.sample_if_due(lambda: None)
        assert lease._next_sample_at == 1010


@mocketize
def test_retains_sample_frequency_when_the_header_is_absent():
    stub_lease(granted="true")
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.granted()
    assert lease.sample_frequency == 15


@mocketize
def test_grants_only_on_a_literal_true():
    stub_lease(granted="1", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert not lease.granted()


@mocketize
def test_clamps_a_garbled_sample_frequency_to_a_sane_floor():
    stub_lease(
        granted="true",
        **{"HireFire-Sample-Frequency": "0"},
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.sample_frequency == Lease.SAMPLE_FREQUENCY_BOUNDS[0]


@mocketize
def test_clamps_an_over_large_sample_frequency_to_the_ceiling():
    stub_lease(
        granted="true",
        **{"HireFire-Sample-Frequency": "99999"},
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.sample_frequency == Lease.SAMPLE_FREQUENCY_BOUNDS[1]


@mocketize
def test_clamps_a_garbled_ttl_to_a_sane_floor():
    stub_lease(
        granted="true",
        **{"HireFire-Lease-TTL": "0"},
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease._ttl == Lease.TTL_BOUNDS[0]


@mocketize
def test_clamps_an_over_large_ttl_to_the_ceiling():
    stub_lease(
        granted="true",
        **{"HireFire-Lease-TTL": "99999"},
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease._ttl == Lease.TTL_BOUNDS[1]


@mocketize
def test_closes_the_underlying_client():
    stub_lease(granted="true")
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    lease.close()
    assert lease._client._connection is None


@mocketize
def test_unauthorized_ignores_frequency_and_ttl_headers():
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=401,
        headers={"HireFire-Sample-Frequency": "99", "HireFire-Lease-TTL": "99"},
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert not lease.granted()
    assert lease.sample_frequency == 15


@mocketize
def test_expiry_paces_off_the_monotonic_clock_not_the_wall_clock():
    stub_lease(granted="true", **{"HireFire-Lease-TTL": "30"})

    mono = [5000.0]
    with freeze_time(at(1000)), patch("time.monotonic", side_effect=lambda: mono[0]):
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)

    assert lease._expires_at == 5030.0


@mocketize
def test_forked_child_reissues_identity_and_re_requests_the_lease():
    stub_lease(granted="true")
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.granted()
    original_process_id = lease.process_id
    lease._expires_at = 0

    polled_ids: list[str] = []
    real_request = lease._client.request_lease

    def capture(process_id: str):
        polled_ids.append(process_id)
        return real_request(process_id)

    child_pid = os.getpid() + 1
    with patch.object(lease._client, "request_lease", side_effect=capture):
        with patch("os.getpid", return_value=child_pid):
            lease.request_if_due(hold=lambda _plan: True)

    assert lease.process_id != original_process_id
    assert polled_ids == [lease.process_id]
    assert lease._owner_pid == child_pid


@mocketize
def test_unauthorized_clears_prior_job_queues():
    body = json.dumps(
        {
            "version": 1,
            "job_queues": [{"name": "worker", "strategy": "jql"}],
        }
    )
    Entry.register(
        Entry.POST,
        LEASE_URL,
        Response(
            body=body,
            status=200,
            headers={
                "HireFire-Lease-Granted": "true",
                "HireFire-Sample-Frequency": "15",
            },
        ),
        Response(body="", status=401),
    )

    with freeze_time(at(1000)) as frozen:
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)
        assert lease.granted()
        assert lease.job_queues

        frozen.move_to(at(1015))
        lease.request_if_due(hold=lambda _plan: True)
        assert not lease.granted()
        assert lease.job_queues == []


@mocketize
def test_deny_after_grant_clears_job_queues_plan():
    body = json.dumps(
        {
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
    )
    Entry.register(
        Entry.POST,
        LEASE_URL,
        Response(
            body=body,
            status=200,
            headers={
                "HireFire-Lease-Granted": "true",
                "HireFire-Sample-Frequency": "15",
            },
        ),
        Response(
            body="",
            status=200,
            headers={
                "HireFire-Lease-Granted": "false",
                "HireFire-Sample-Frequency": "15",
            },
        ),
    )

    with freeze_time(at(1000)) as frozen:
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)
        assert lease.granted()
        assert lease.job_queues

        frozen.move_to(at(1015))
        lease.request_if_due(hold=lambda _plan: True)
        assert not lease.granted()
        assert lease.job_queues == []


@mocketize
def test_transport_failure_clears_prior_job_queues():
    body = json.dumps(
        {
            "version": 1,
            "job_queues": [{"name": "worker", "strategy": "jql"}],
        }
    )
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body=body,
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.job_queues

    lease._expires_at = 0
    with patch.object(lease._client, "request_lease", side_effect=RequestError("boom")):
        with pytest.raises(RequestError):
            lease.request_if_due(hold=lambda _plan: True)
    assert not lease.granted()
    assert lease.job_queues == []


@mocketize
def test_non_object_or_non_array_plan_body_yields_empty_job_queues():
    for body in [
        json.dumps([]),
        json.dumps("string"),
        json.dumps({"version": 1, "job_queues": {}}),
    ]:
        Entry.single_register(
            Entry.POST,
            LEASE_URL,
            status=200,
            headers={
                "HireFire-Lease-Granted": "true",
                "HireFire-Sample-Frequency": "15",
            },
            body=body,
        )
        lease = Lease()
        lease.request_if_due(hold=lambda _plan: True)
        assert lease.granted(), body
        assert lease.job_queues == [], body


@mocketize
def test_hold_receives_parsed_job_queues():
    entry = {
        "name": "worker",
        "strategy": "jql",
        "adapter": "celery",
        "queues": ["default"],
        "options": {},
    }
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body=json.dumps({"version": 1, "job_queues": [entry]}),
    )
    received = []

    def hold(queues):
        received.extend(queues)
        return True

    lease = Lease()
    lease.request_if_due(hold=hold)
    assert len(received) == 1
    assert received[0]["name"] == "worker"
    assert received[0]["adapter"] == "celery"
    assert lease.trace() is False


@mocketize
def test_parses_grant_job_queues_body():
    body = json.dumps(
        {
            "version": 1,
            "job_queues": [
                {
                    "name": "  worker  ",
                    "strategy": " jql ",
                    "adapter": "celery",
                    "queues": ["default"],
                },
                {"name": "", "strategy": "jql"},
                {"name": "mailer", "strategy": "jqs"},
            ],
        }
    )
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body=body,
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.granted()
    assert len(lease.job_queues) == 2
    assert lease.job_queues[0]["name"] == "worker"
    assert lease.job_queues[0]["strategy"] == "jql"
    assert lease.job_queues[0]["adapter"] == "celery"
    assert lease.job_queues[1]["name"] == "mailer"


@mocketize
def test_json_null_adapter_is_strategy_only():
    body = json.dumps(
        {
            "version": 1,
            "job_queues": [
                {
                    "name": "worker",
                    "strategy": "jql",
                    "adapter": None,
                    "queues": ["default"],
                },
                {"name": None, "strategy": "jql", "adapter": "celery"},
                {"name": "mailer", "strategy": None},
            ],
        }
    )
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "true",
            "HireFire-Sample-Frequency": "15",
        },
        body=body,
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.granted()
    assert len(lease.job_queues) == 1
    entry = lease.job_queues[0]
    assert entry["name"] == "worker"
    assert entry["strategy"] == "jql"
    assert entry.get("adapter") == ""
    from hirefire_resource.dispatcher import Dispatcher

    assert not Dispatcher._adapter_present(entry)


@mocketize
def test_clamps_a_non_numeric_sample_frequency_to_the_floor():
    stub_lease(
        granted="true",
        **{"HireFire-Sample-Frequency": "abc"},
    )
    lease = Lease()
    lease.request_if_due(hold=lambda _plan: True)
    assert lease.sample_frequency == Lease.SAMPLE_FREQUENCY_BOUNDS[0]


def test_parse_grant_body_preserves_trace_for_non_array_job_queues():
    grant = Lease()._parse_grant_body(
        json.dumps({"version": 1, "trace": True, "job_queues": {}})
    )

    assert grant.trace is True
    assert grant.job_queues == []
