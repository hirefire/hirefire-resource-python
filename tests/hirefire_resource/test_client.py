import http.client
import socket
import ssl
from unittest.mock import patch

import pytest
from mocket import Mocket, mocketize
from mocket.mockhttp import Entry

from hirefire_resource.client import Client, RequestError
from hirefire_resource.version import VERSION
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa: F401

PAYLOAD = '[{"name":"web","samples":{"1000":[]}}]'
INGEST_URL = "https://data.hirefire.io/metrics/ingest"
LEASE_URL = "https://data.hirefire.io/metrics/lease"


@pytest.fixture
def client():
    return Client()


@mocketize
def test_submit_samples_sends_payload(client, set_HIREFIRE_TOKEN):
    Entry.single_register(Entry.POST, INGEST_URL, status=200)

    client.submit_samples(PAYLOAD)

    request = Mocket.last_request()
    assert request.method == "POST"
    assert request.body == PAYLOAD
    assert request.headers.get("content-type") == "application/json"
    assert request.headers.get("hirefire-token") == HIREFIRE_TOKEN
    assert request.headers.get("hirefire-agent") == f"Python-{VERSION}"


@mocketize
def test_submit_samples_returns_none_on_unauthorized(client, set_HIREFIRE_TOKEN):
    Entry.single_register(Entry.POST, INGEST_URL, status=401)
    assert client.submit_samples(PAYLOAD) is None


@mocketize
def test_submit_samples_raises_on_server_error(client, set_HIREFIRE_TOKEN):
    Entry.single_register(Entry.POST, INGEST_URL, status=500)

    with pytest.raises(RequestError) as exc_info:
        client.submit_samples(PAYLOAD)

    assert "500" in str(exc_info.value)


@mocketize
def test_submit_samples_raises_on_unexpected_status(client, set_HIREFIRE_TOKEN):
    Entry.single_register(Entry.POST, INGEST_URL, status=422)

    with pytest.raises(RequestError):
        client.submit_samples(PAYLOAD)


def test_submit_samples_raises_on_timeout(client, set_HIREFIRE_TOKEN):
    with patch("http.client.HTTPSConnection.request", side_effect=socket.timeout):
        with pytest.raises(RequestError) as exc_info:
            client.submit_samples(PAYLOAD)

    assert "timed out" in str(exc_info.value)


def test_submit_samples_raises_on_transport_errors(client, set_HIREFIRE_TOKEN):
    transport_errors = [
        socket.gaierror("name resolution failed"),
        ConnectionRefusedError("connection refused"),
        BrokenPipeError("broken pipe"),
        ssl.SSLError("certificate verify failed"),
        http.client.HTTPException("protocol error"),
    ]

    for transport_error in transport_errors:
        with patch("http.client.HTTPSConnection.request", side_effect=transport_error):
            with pytest.raises(RequestError) as exc_info:
                client.submit_samples(PAYLOAD)

        assert "Network error" in str(exc_info.value)


@mocketize
def test_request_lease_sends_process_id(client, set_HIREFIRE_TOKEN):
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={
            "HireFire-Lease-Granted": "false",
            "HireFire-Sample-Frequency": "15",
        },
    )

    client.request_lease("abc123")

    request = Mocket.last_request()
    assert request.headers.get("hirefire-token") == HIREFIRE_TOKEN
    assert request.headers.get("hirefire-process-id") == "abc123"


def test_request_lease_raises_on_timeout(client, set_HIREFIRE_TOKEN):
    with patch("http.client.HTTPSConnection.request", side_effect=socket.timeout):
        with pytest.raises(RequestError):
            client.request_lease("abc123")


def test_raises_without_token(client):
    with pytest.raises(RequestError) as exc_info:
        client.submit_samples("[]")

    assert "HIREFIRE_TOKEN" in str(exc_info.value)


@mocketize
def test_custom_data_url(set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "https://custom.hirefire.io")
    Entry.single_register(
        Entry.POST, "https://custom.hirefire.io/metrics/ingest", status=200
    )

    Client().submit_samples(PAYLOAD)

    assert Mocket.last_request().headers.get("host") == "custom.hirefire.io"


@mocketize
def test_custom_data_url_over_plain_http(set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "http://localhost:9999")
    Entry.single_register(
        Entry.POST, "http://localhost:9999/metrics/ingest", status=200
    )

    Client().submit_samples(PAYLOAD)

    assert Mocket.last_request().method == "POST"


@mocketize
def test_honors_a_path_prefix_in_the_data_url(set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "https://custom.hirefire.io/prefix")
    Entry.single_register(
        Entry.POST, "https://custom.hirefire.io/prefix/metrics/ingest", status=200
    )

    Client().submit_samples(PAYLOAD)

    assert Mocket.last_request().path == "/prefix/metrics/ingest"


@mocketize
def test_request_lease_omits_the_agent_header(client, set_HIREFIRE_TOKEN):
    # Ingest sends HireFire-Agent; the lease deliberately does not.
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={"HireFire-Lease-Granted": "false"},
    )

    client.request_lease("abc123")

    header_names = [name.lower() for name in Mocket.last_request().headers.keys()]
    assert "hirefire-agent" not in header_names
