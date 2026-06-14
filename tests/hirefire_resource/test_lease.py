import re
from unittest.mock import patch

import httpretty
import pytest
from freezegun import freeze_time

from hirefire_resource.client import RequestError
from hirefire_resource.lease import Lease
from tests.helpers import at, set_HIREFIRE_TOKEN  # noqa: F401

LEASE_URL = "https://data.hirefire.io/metrics/lease"


# Every lease test needs a token (the client requires one to poll); the explicit
# dependency on set_HIREFIRE_TOKEN guarantees the reset/env fixtures run first.
@pytest.fixture(autouse=True)
def with_token(set_HIREFIRE_TOKEN):
    pass


def stub_lease(granted="false", **headers):
    adding_headers = {"HireFire-Lease-Granted": granted}
    adding_headers.update(headers)
    httpretty.register_uri(
        httpretty.POST, LEASE_URL, status=200, adding_headers=adding_headers
    )


def test_process_id_is_stable_uuid():
    lease = Lease()
    assert re.match(
        r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
        lease.process_id,
    )
    assert lease.process_id == lease.process_id


def test_not_granted_by_default():
    assert not Lease().granted()


@httpretty.activate
def test_granted_after_successful_poll():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    assert lease.granted()


@httpretty.activate
def test_denied_after_poll():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    assert not lease.granted()


@httpretty.activate
def test_updates_sample_frequency_from_response():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "30"})
    lease = Lease()
    lease.request_if_due()
    assert lease.sample_frequency == 30


@httpretty.activate
def test_updates_ttl_from_response():
    stub_lease(granted="false", **{"HireFire-Lease-TTL": "30"})
    lease = Lease()
    lease.request_if_due()
    assert lease._ttl == 30


@httpretty.activate
def test_not_polled_before_interval_elapsed():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    lease.request_if_due()
    assert len(httpretty.latest_requests()) == 1


@httpretty.activate
def test_silently_denied_on_unauthorized():
    httpretty.register_uri(httpretty.POST, LEASE_URL, status=401)
    lease = Lease()
    lease.request_if_due()
    assert not lease.granted()


@httpretty.activate
def test_revokes_granted_lease_on_unauthorized():
    httpretty.register_uri(
        httpretty.POST,
        LEASE_URL,
        responses=[
            httpretty.Response(
                body="",
                status=200,
                adding_headers={
                    "HireFire-Lease-Granted": "true",
                    "HireFire-Sample-Frequency": "15",
                },
            ),
            httpretty.Response(body="", status=401),
        ],
    )

    with freeze_time(at(1000)) as frozen:
        lease = Lease()
        lease.request_if_due()
        assert lease.granted()

        frozen.move_to(at(1015))
        lease.request_if_due()
        assert not lease.granted()


def test_transport_failure_demotes_and_waits_a_full_ttl():
    with patch(
        "http.client.HTTPSConnection.request", side_effect=ConnectionRefusedError
    ) as mock_request:
        lease = Lease()
        with pytest.raises(RequestError):
            lease.request_if_due()
        assert not lease.granted()

        lease.request_if_due()  # not due again until the TTL elapses
        assert mock_request.call_count == 1


@httpretty.activate
def test_transport_failure_revokes_granted_lease():
    stub_lease(granted="true")

    with freeze_time(at(1000)) as frozen:
        lease = Lease()
        lease.request_if_due()
        assert lease.granted()

        with patch(
            "http.client.HTTPSConnection.request", side_effect=ConnectionRefusedError
        ):
            frozen.move_to(at(1015))
            with pytest.raises(RequestError):
                lease.request_if_due()
            assert not lease.granted()


@httpretty.activate
def test_ttl_update_applies_to_the_current_window():
    stub_lease(granted="true", **{"HireFire-Lease-TTL": "30"})

    with freeze_time(at(1000)):
        lease = Lease()
        lease.request_if_due()

    assert lease._expires_at == 1030


@httpretty.activate
def test_raises_on_server_error():
    httpretty.register_uri(httpretty.POST, LEASE_URL, status=500)
    lease = Lease()

    with pytest.raises(RequestError) as exc_info:
        lease.request_if_due()

    assert "Lease request failed" in str(exc_info.value)
    assert not lease.granted()


@httpretty.activate
def test_sends_process_id_header():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    assert (
        httpretty.last_request().headers.get("HireFire-Process-ID") == lease.process_id
    )


@httpretty.activate
def test_disabled_lease_skips_request():
    httpretty.register_uri(httpretty.POST, LEASE_URL, status=200)
    disabled = Lease(enabled=False)
    disabled.request_if_due()
    assert len(httpretty.latest_requests()) == 0
    assert not disabled.granted()


@httpretty.activate
def test_sample_if_due_yields_when_granted_and_due():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == [True]


@httpretty.activate
def test_sample_if_due_skips_when_not_granted():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == []


@httpretty.activate
def test_sample_if_due_skips_when_not_yet_due():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    lease.sample_if_due(lambda: None)  # first sample — due immediately

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))  # second — not yet due
    assert sampled == []


@httpretty.activate
def test_failed_sample_consumes_its_window():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()

    def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        lease.sample_if_due(boom)

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == []  # the raising sample consumed this window; no retry-per-tick


@httpretty.activate
def test_sample_if_due_advances_next_sample_at():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "10"})

    with freeze_time(at(1000)):
        lease = Lease()
        lease.request_if_due()
        lease.sample_if_due(lambda: None)
        assert lease._next_sample_at == 1010


@httpretty.activate
def test_retains_sample_frequency_when_the_header_is_absent():
    stub_lease(granted="true")  # no HireFire-Sample-Frequency header
    lease = Lease()
    lease.request_if_due()
    assert lease.granted()
    assert lease.sample_frequency == 15  # default retained


@httpretty.activate
def test_grants_only_on_a_literal_true():
    stub_lease(granted="1", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    assert not lease.granted()  # only the exact string "true" grants


@httpretty.activate
def test_unauthorized_ignores_frequency_and_ttl_headers():
    httpretty.register_uri(
        httpretty.POST,
        LEASE_URL,
        status=401,
        adding_headers={"HireFire-Sample-Frequency": "99", "HireFire-Lease-TTL": "99"},
    )
    lease = Lease()
    lease.request_if_due()
    assert not lease.granted()
    assert lease.sample_frequency == 15  # a 401 returns before reading headers
