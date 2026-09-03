import email.message
import http.client
import os
import socket
import ssl
from unittest.mock import patch

import pytest
from mocket import Mocket, mocketize
from mocket.mockhttp import Entry

from hirefire_resource.client import PAYLOAD_TOO_LARGE, Client, RequestError
from hirefire_resource.version import VERSION
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa: F401

PAYLOAD = '[{"name":"web","metrics":{"rqt":{"1000":[]}}}]'
INGEST_URL = "https://data.hirefire.io/metrics/ingest"
LEASE_URL = "https://data.hirefire.io/metrics/lease"


@pytest.fixture
def client():
    return Client()


class FakeResponse:
    def __init__(self, status=200, read_error=None):
        self.status = status
        self.headers = email.message.Message()
        self._read_error = read_error

    def read(self):
        if self._read_error is not None:
            raise self._read_error
        return b""

    def close(self):
        pass


class FakeConnection:
    scripts: list = []
    created: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port if port is not None else 443
        self.timeout = timeout
        self.sock = None
        self._outcomes = FakeConnection.scripts.pop(0) if FakeConnection.scripts else []
        FakeConnection.created.append(self)

    def request(self, method, path, body, headers):
        self.sock = object()
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        self._pending = outcome

    def getresponse(self):
        return self._pending

    def close(self):
        self.sock = None


@pytest.fixture
def fake_connections():
    FakeConnection.scripts = []
    FakeConnection.created = []
    with patch("http.client.HTTPSConnection", FakeConnection):
        yield FakeConnection


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


@mocketize
def test_submit_samples_returns_payload_too_large_on_413(client, set_HIREFIRE_TOKEN):
    Entry.single_register(
        Entry.POST, "https://data.hirefire.io/metrics/ingest", status=413
    )
    assert client.submit_samples(PAYLOAD) == PAYLOAD_TOO_LARGE


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


def test_reuses_a_single_connection_across_requests(
    client, set_HIREFIRE_TOKEN, fake_connections
):
    fake_connections.scripts = [[FakeResponse(200), FakeResponse(200)]]

    client.submit_samples("[]")
    first = client._connection
    client.submit_samples("[]")

    assert client._connection is first
    assert len(fake_connections.created) == 1
    assert first.sock is not None


def test_reconnects_and_retries_once_on_a_stale_keep_alive_socket(
    client, set_HIREFIRE_TOKEN, fake_connections
):
    fake_connections.scripts = [
        [FakeResponse(200), ConnectionResetError("peer reset")],
        [FakeResponse(200)],
    ]

    client.submit_samples("[]")
    established = client._connection

    result = client.submit_samples("[]")

    assert result is not None
    assert client._connection is not established
    assert len(fake_connections.created) == 2


def test_does_not_retry_a_cold_connection_failure(
    client, set_HIREFIRE_TOKEN, fake_connections
):
    fake_connections.scripts = [[ConnectionResetError("peer reset")]]

    with pytest.raises(RequestError):
        client.submit_samples("[]")

    assert len(fake_connections.created) == 1


def test_opens_a_fresh_connection_in_a_forked_child(
    client, set_HIREFIRE_TOKEN, fake_connections
):
    fake_connections.scripts = [[FakeResponse(200)], [FakeResponse(200)]]

    client.submit_samples("[]")
    inherited = client._connection

    client._owner_pid = os.getpid() - 1

    client.submit_samples("[]")

    assert client._connection is not inherited
    assert client._owner_pid == os.getpid()
    assert len(fake_connections.created) == 2


def test_reconnects_and_retries_once_on_a_desynced_keep_alive_response(
    client, set_HIREFIRE_TOKEN, fake_connections
):
    fake_connections.scripts = [
        [FakeResponse(200), http.client.BadStatusLine("garbled")],
        [FakeResponse(200)],
    ]

    client.submit_samples("[]")
    established = client._connection

    result = client.submit_samples("[]")

    assert result is not None
    assert client._connection is not established
    assert len(fake_connections.created) == 2


def test_reconnects_and_retries_once_on_an_incomplete_keep_alive_read(
    client, set_HIREFIRE_TOKEN, fake_connections
):
    fake_connections.scripts = [
        [
            FakeResponse(200),
            FakeResponse(200, read_error=http.client.IncompleteRead(b"")),
        ],
        [FakeResponse(200)],
    ]

    client.submit_samples("[]")
    established = client._connection

    result = client.submit_samples("[]")

    assert result is not None
    assert client._connection is not established
    assert len(fake_connections.created) == 2


@mocketize
def test_close_finishes_and_clears_the_persistent_connection(
    client, set_HIREFIRE_TOKEN
):
    Entry.single_register(Entry.POST, INGEST_URL, status=200)
    client.submit_samples("[]")
    assert client._connection is not None

    client.close()

    assert client._connection is None


def test_close_is_safe_without_a_connection(client):
    client.close()

    assert client._connection is None


def test_close_swallows_a_failing_connection_shutdown(client):
    class FailingConnection:
        def close(self):
            raise OSError("already closed")

    client._connection = FailingConnection()
    client._owner_pid = os.getpid()

    client.close()

    assert client._connection is None


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
    assert request.headers.get("hirefire-agent") == f"Python-{VERSION}"
    assert request.headers.get("hirefire-process-id") == "abc123"


def test_request_lease_raises_on_timeout(client, set_HIREFIRE_TOKEN):
    with patch("http.client.HTTPSConnection.request", side_effect=socket.timeout):
        with pytest.raises(RequestError):
            client.request_lease("abc123")


def test_raises_without_token(client):
    with pytest.raises(RequestError) as exc_info:
        client.submit_samples("[]")

    assert "HIREFIRE_TOKEN" in str(exc_info.value)


def test_raises_with_empty_token(client, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "")

    with pytest.raises(RequestError) as exc_info:
        client.submit_samples("[]")

    assert "HIREFIRE_TOKEN" in str(exc_info.value)


def test_blank_and_slash_only_data_url_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "   ")
    assert Client()._base_url() == "https://data.hirefire.io"
    monkeypatch.setenv("HIREFIRE_DATA_URL", "///")
    assert Client()._base_url() == "https://data.hirefire.io"
    monkeypatch.setenv("HIREFIRE_DATA_URL", " https://example.test/path/ ")
    assert Client()._base_url() == "https://example.test/path"


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
def test_custom_data_url_with_a_trailing_slash_does_not_double_the_path(
    set_HIREFIRE_TOKEN, monkeypatch
):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "https://custom.hirefire.io/prefix/")
    Entry.single_register(
        Entry.POST, "https://custom.hirefire.io/prefix/metrics/ingest", status=200
    )

    Client().submit_samples(PAYLOAD)

    assert Mocket.last_request().path == "/prefix/metrics/ingest"


@mocketize
def test_custom_data_url_honors_a_path_prefix(set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "https://custom.hirefire.io/prefix")
    Entry.single_register(
        Entry.POST, "https://custom.hirefire.io/prefix/metrics/ingest", status=200
    )

    Client().submit_samples(PAYLOAD)

    assert Mocket.last_request().path == "/prefix/metrics/ingest"


@mocketize
def test_request_lease_sends_the_agent_header(client, set_HIREFIRE_TOKEN):
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={"HireFire-Lease-Granted": "false"},
    )

    client.request_lease("abc123")

    assert Mocket.last_request().headers.get("hirefire-agent") == f"Python-{VERSION}"


def test_does_not_retry_twice_on_persistent_stale_errors(
    client, set_HIREFIRE_TOKEN, fake_connections
):
    fake_connections.scripts = [
        [FakeResponse(200), ConnectionResetError("peer reset")],
        [ConnectionResetError("peer reset again")],
    ]

    client.submit_samples("[]")

    with pytest.raises(RequestError):
        client.submit_samples("[]")

    assert len(fake_connections.created) == 2


def test_reconnects_when_the_keep_alive_socket_sat_idle_past_the_timeout(
    client, set_HIREFIRE_TOKEN, fake_connections
):
    fake_connections.scripts = [[FakeResponse(200)], [FakeResponse(200)]]

    client.submit_samples("[]")
    established = client._connection

    client._last_used_at -= Client.KEEP_ALIVE_TIMEOUT + 1

    client.submit_samples("[]")

    assert client._connection is not established
    assert len(fake_connections.created) == 2


def test_reuses_a_keep_alive_socket_still_within_the_timeout(
    client, set_HIREFIRE_TOKEN, fake_connections
):
    fake_connections.scripts = [[FakeResponse(200), FakeResponse(200)]]

    client.submit_samples("[]")
    established = client._connection

    client._last_used_at -= Client.KEEP_ALIVE_TIMEOUT - 1

    client.submit_samples("[]")

    assert client._connection is established
    assert len(fake_connections.created) == 1


@mocketize
def test_request_lease_returns_response_body(client, set_HIREFIRE_TOKEN):
    body = '{"version":1,"job_queues":[]}'
    Entry.single_register(
        Entry.POST,
        LEASE_URL,
        status=200,
        headers={"HireFire-Lease-Granted": "true"},
        body=body,
    )
    response = client.request_lease("pid-1")
    assert response.body == body
