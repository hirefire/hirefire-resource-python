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


# Every lease test needs a token (the client requires one to poll). The explicit
# dependency on set_HIREFIRE_TOKEN guarantees the reset/env fixtures run first.
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
    lease.request_if_due()
    assert lease.granted()


@mocketize
def test_denied_after_poll():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    assert not lease.granted()


@mocketize
def test_updates_sample_frequency_from_response():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "30"})
    lease = Lease()
    lease.request_if_due()
    assert lease.sample_frequency == 30


@mocketize
def test_updates_ttl_from_response():
    stub_lease(granted="false", **{"HireFire-Lease-TTL": "30"})
    lease = Lease()
    lease.request_if_due()
    assert lease._ttl == 30


@mocketize
def test_not_polled_before_interval_elapsed():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    lease.request_if_due()
    assert len(Mocket.request_list()) == 1


@mocketize
def test_silently_denied_on_unauthorized():
    Entry.single_register(Entry.POST, LEASE_URL, status=401)
    lease = Lease()
    lease.request_if_due()
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


@mocketize
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


@mocketize
def test_ttl_update_applies_to_the_current_window():
    stub_lease(granted="true", **{"HireFire-Lease-TTL": "30"})

    with freeze_time(at(1000)):
        lease = Lease()
        lease.request_if_due()

    assert lease._expires_at == 1030


@mocketize
def test_raises_on_server_error():
    Entry.single_register(Entry.POST, LEASE_URL, status=500)
    lease = Lease()

    with pytest.raises(RequestError) as exc_info:
        lease.request_if_due()

    assert "Lease request failed" in str(exc_info.value)
    assert not lease.granted()


@mocketize
def test_sends_process_id_header():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    assert Mocket.last_request().headers.get("hirefire-process-id") == lease.process_id


@mocketize
def test_disabled_lease_skips_request():
    Entry.single_register(Entry.POST, LEASE_URL, status=200)
    disabled = Lease(enabled=False)
    disabled.request_if_due()
    assert len(Mocket.request_list()) == 0
    assert not disabled.granted()


@mocketize
def test_sample_if_due_yields_when_granted_and_due():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == [True]


@mocketize
def test_sample_if_due_skips_when_not_granted():
    stub_lease(granted="false", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == []


@mocketize
def test_sample_if_due_skips_when_not_yet_due():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    lease.sample_if_due(lambda: None)

    sampled = []
    lease.sample_if_due(lambda: sampled.append(True))
    assert sampled == []


@mocketize
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
    assert sampled == []  # the raising sample consumed this window, no retry-per-tick


@mocketize
def test_sample_if_due_advances_next_sample_at():
    stub_lease(granted="true", **{"HireFire-Sample-Frequency": "10"})

    with freeze_time(at(1000)):
        lease = Lease()
        lease.request_if_due()
        lease.sample_if_due(lambda: None)
        assert lease._next_sample_at == 1010


@mocketize
def test_retains_sample_frequency_when_the_header_is_absent():
    stub_lease(granted="true")  # no HireFire-Sample-Frequency header
    lease = Lease()
    lease.request_if_due()
    assert lease.granted()
    assert lease.sample_frequency == 15  # default retained


@mocketize
def test_grants_only_on_a_literal_true():
    stub_lease(granted="1", **{"HireFire-Sample-Frequency": "15"})
    lease = Lease()
    lease.request_if_due()
    assert not lease.granted()  # only the exact string "true" grants


@mocketize
def test_clamps_a_garbled_sample_frequency_to_a_sane_floor():
    stub_lease(
        granted="true",
        **{"HireFire-Sample-Frequency": "0"},  # a bad header must not sample every tick
    )
    lease = Lease()
    lease.request_if_due()
    assert lease.sample_frequency == Lease.SAMPLE_FREQUENCY_BOUNDS[0]


@mocketize
def test_clamps_a_garbled_ttl_to_a_sane_floor():
    stub_lease(
        granted="true",
        **{"HireFire-Lease-TTL": "0"},  # a bad header must not re-request every tick
    )
    lease = Lease()
    lease.request_if_due()
    assert lease._ttl == Lease.TTL_BOUNDS[0]


@mocketize
def test_closes_the_underlying_client():
    stub_lease(granted="true")
    lease = Lease()
    lease.request_if_due()
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
    lease.request_if_due()
    assert not lease.granted()
    assert lease.sample_frequency == 15  # a 401 returns before reading headers
