import email.message
import os
import socket
import time

import pytest
from mocket import Mocket, mocketize
from mocket.mockhttp import Entry

from hirefire_resource import HireFire
from hirefire_resource.client import Client, Response
from hirefire_resource.configuration import Configuration
from hirefire_resource.errors import RequestError
from hirefire_resource.version import VERSION
from tests.helpers import HIREFIRE_TOKEN, set_HIREFIRE_TOKEN  # noqa: F401

PAYLOAD = '[{"name":"web","metrics":{"rqt":{"1000":[]}}}]'
INGEST_URL = "https://data.hirefire.io/metrics/ingest"
LEASE_URL = "https://data.hirefire.io/metrics/lease"


@pytest.fixture
def client():
    return Client(HireFire.configuration)


@pytest.fixture
def server(fake_server, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", HIREFIRE_TOKEN)
    monkeypatch.setenv("HIREFIRE_DATA_URL", fake_server.url)
    return fake_server


def replies(server, *actions):
    queue = list(actions)
    server.handler = lambda request: queue.pop(0) if queue else {"status": 200}


def seconds_until_failure(call, message):
    started = time.monotonic()
    with pytest.raises(RequestError) as failure:
        call()
    assert message in str(failure.value)
    return time.monotonic() - started


def closed_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


def test_sends_the_token_of_the_configuration_it_was_given(server):
    other = Configuration()
    other.token = "the-token-of-another-configuration"

    Client(other).submit_samples(PAYLOAD)
    Client(HireFire.configuration).submit_samples(PAYLOAD)

    assert [request.header("HireFire-Token") for request in server.requests] == [
        "the-token-of-another-configuration",
        HIREFIRE_TOKEN,
    ]


@mocketize
def test_submit_samples_returns_the_response_of_a_rejected_token(
    client, set_HIREFIRE_TOKEN
):
    Entry.single_register(
        Entry.POST,
        INGEST_URL,
        status=401,
        headers={"HireFire-Dispatch-Frequency": "30"},
    )

    response = client.submit_samples(PAYLOAD)

    assert response.unauthorized()
    assert not response.ok()
    assert not response.too_large()
    assert response.headers.get("HireFire-Dispatch-Frequency") == "30"


@mocketize
def test_submit_samples_raises_on_server_error(client, set_HIREFIRE_TOKEN):
    Entry.single_register(Entry.POST, INGEST_URL, status=500)

    with pytest.raises(RequestError) as exc_info:
        client.submit_samples(PAYLOAD)

    assert str(exc_info.value) == "Ingest request failed with 500 status."


@mocketize
def test_submit_samples_raises_on_unexpected_status(client, set_HIREFIRE_TOKEN):
    Entry.single_register(Entry.POST, INGEST_URL, status=422)

    with pytest.raises(RequestError) as exc_info:
        client.submit_samples(PAYLOAD)

    assert str(exc_info.value) == "Ingest request failed with 422 status."


@mocketize
def test_submit_samples_returns_the_response_of_a_payload_too_large(
    client, set_HIREFIRE_TOKEN
):
    Entry.single_register(Entry.POST, INGEST_URL, status=413)

    response = client.submit_samples(PAYLOAD)

    assert response.too_large()
    assert not response.ok()
    assert not response.unauthorized()


@mocketize
def test_submit_samples_returns_the_response_of_a_success(client, set_HIREFIRE_TOKEN):
    Entry.single_register(Entry.POST, INGEST_URL, status=204)

    response = client.submit_samples(PAYLOAD)

    assert response.ok()
    assert not response.unauthorized()
    assert not response.too_large()


@pytest.mark.parametrize("status", [199, 300])
def test_a_status_next_to_the_2xx_range_is_not_ok(status):
    assert not Response(status, email.message.Message()).ok()


def test_a_request_ends_at_its_timeout_when_the_server_never_answers(server):
    server.handler = lambda request: {"then": "stall"}
    client = Client(HireFire.configuration, timeout=0.3)

    elapsed = seconds_until_failure(
        lambda: client.submit_samples(PAYLOAD), "Request timed out."
    )

    assert 0.3 <= elapsed < 2
    assert client._connection is None


def test_a_lease_request_ends_at_its_timeout_when_the_server_never_answers(server):
    server.handler = lambda request: {"then": "stall"}

    elapsed = seconds_until_failure(
        lambda: Client(HireFire.configuration, timeout=0.3).request_lease("abc123"),
        "Request timed out.",
    )

    assert 0.3 <= elapsed < 2


def test_a_request_ends_at_its_timeout_when_the_response_arrives_byte_by_byte(server):
    server.handler = lambda request: {"status": 200, "body": "x" * 200, "drip": 0.02}
    client = Client(HireFire.configuration, timeout=0.3)

    elapsed = seconds_until_failure(
        lambda: client.request_lease("abc123"), "Request timed out."
    )

    assert 0.3 <= elapsed < 2
    assert client._connection is None


def test_a_request_ends_at_its_timeout_when_the_tls_handshake_stalls(
    server, monkeypatch
):
    monkeypatch.setenv("HIREFIRE_DATA_URL", f"https://127.0.0.1:{server.port}")
    server.on_accept = "stall"

    elapsed = seconds_until_failure(
        lambda: Client(HireFire.configuration, timeout=0.3).submit_samples(PAYLOAD),
        "Request timed out.",
    )

    assert 0.3 <= elapsed < 2


def test_a_tls_handshake_answered_in_plain_text_is_a_network_error(server, monkeypatch):
    monkeypatch.setenv("HIREFIRE_DATA_URL", f"https://127.0.0.1:{server.port}")
    server.on_accept = b"HTTP/1.1 400 Bad Request\r\n\r\n"
    client = Client(HireFire.configuration)

    elapsed = seconds_until_failure(
        lambda: client.submit_samples(PAYLOAD), "Network error (SSLError"
    )

    assert elapsed < 2
    assert client._connection is None


def test_a_refused_connection_is_a_network_error(server, client):
    server.stop()

    with pytest.raises(RequestError) as failure:
        client.submit_samples(PAYLOAD)

    assert "Network error (ConnectionRefusedError" in str(failure.value)


def test_a_host_that_does_not_resolve_is_a_network_error(
    client, set_HIREFIRE_TOKEN, monkeypatch
):
    def unresolvable(*args, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", unresolvable)

    with pytest.raises(RequestError) as failure:
        client.submit_samples(PAYLOAD)

    assert str(failure.value) == "Network error (gaierror: name resolution failed)."


def test_a_host_without_an_address_is_a_network_error(
    client, set_HIREFIRE_TOKEN, monkeypatch
):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [])

    with pytest.raises(RequestError) as failure:
        client.submit_samples(PAYLOAD)

    assert str(failure.value) == (
        "Network error (OSError: No address found for data.hirefire.io.)."
    )


def test_connects_to_the_next_address_when_the_first_refuses(
    server, client, allow_network, monkeypatch
):
    refusing = ("127.0.0.1", closed_port())
    allow_network(refusing)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)
            for address in (refusing, server.address)
        ],
    )

    assert client.submit_samples(PAYLOAD).ok()
    assert server.accepted == 1


def test_the_time_spent_on_an_address_that_refuses_counts_against_the_timeout(
    server, allow_network, monkeypatch
):
    refusing = ("127.0.0.1", closed_port())
    allow_network(refusing)
    connect = socket.socket.connect

    def slow_to_refuse(self, address):
        if address == refusing:
            time.sleep(0.4)
        return connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", slow_to_refuse)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)
            for address in (refusing, server.address)
        ],
    )

    with pytest.raises(RequestError) as failure:
        Client(HireFire.configuration, timeout=0.3).submit_samples(PAYLOAD)

    assert str(failure.value) == "Request timed out."
    assert server.accepted == 0


def test_a_response_that_is_not_http_is_a_network_error(server, client):
    server.handler = lambda request: {"raw": b"garbage\r\n\r\n", "after": "close"}

    with pytest.raises(RequestError) as failure:
        client.submit_samples(PAYLOAD)

    assert "Network error (BadStatusLine" in str(failure.value)
    assert client._connection is None


def test_a_response_that_announces_a_body_over_the_limit_fails_before_it_is_read(
    server, client
):
    head = f"HTTP/1.1 200 OK\r\nContent-Length: {Client.MAX_BODY_BYTES + 1}\r\n\r\n"
    server.handler = lambda request: {"raw": head.encode(), "after": "stall"}

    elapsed = seconds_until_failure(
        lambda: client.request_lease("abc123"),
        "Response body exceeded 131072 bytes (status 200).",
    )

    assert elapsed < 2
    assert client._connection is None


def test_a_response_without_a_length_fails_once_it_passes_the_limit(server, client):
    chunk = b"10000\r\n" + b"x" * 65_536 + b"\r\n"
    head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
    server.handler = lambda request: {"raw": head + chunk * 3, "after": "stall"}

    elapsed = seconds_until_failure(
        lambda: client.request_lease("abc123"),
        "Response body exceeded 131072 bytes (status 200).",
    )

    assert elapsed < 2
    assert client._connection is None


def test_a_body_of_exactly_the_limit_is_returned_and_the_connection_is_kept(
    server, client
):
    body = "x" * Client.MAX_BODY_BYTES
    server.handler = lambda request: {"status": 200, "body": body}

    assert client.request_lease("abc123").body == body
    assert client.request_lease("abc123").body == body
    assert server.accepted == 1


def test_a_chunked_body_under_the_limit_is_returned(server, client):
    head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
    server.handler = lambda request: {"raw": head + b"3\r\nabc\r\n0\r\n\r\n"}

    assert client.request_lease("abc123").body == "abc"
    assert client.request_lease("abc123").body == "abc"
    assert server.accepted == 1


def test_a_body_cut_short_is_a_network_error(server, client):
    cut = b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc"
    server.handler = lambda request: {"raw": cut, "after": "close"}

    with pytest.raises(RequestError) as failure:
        client.request_lease("abc123")

    assert "Network error (IncompleteRead" in str(failure.value)
    assert client._connection is None


def test_sends_a_payload_larger_than_one_write_in_full(server, client):
    payload = "[" + "1," * 40_000 + "1]"

    assert client.submit_samples(payload).ok()
    assert server.requests[0].body == payload
    assert server.requests[0].header("Content-Length") == str(len(payload))


def test_reuses_a_single_connection_across_requests(server, client):
    client.submit_samples("[]")
    client.submit_samples("[]")

    assert server.accepted == 1
    assert len(server.requests) == 2


@pytest.mark.parametrize("ending", ["close", "reset"])
def test_reconnects_and_retries_once_when_the_server_ended_the_kept_connection(
    server, client, ending
):
    replies(server, {"status": 200, "after": ending})
    client.submit_samples("[]")

    assert client.submit_samples("[]").ok()
    assert server.accepted == 2
    assert len(server.requests) == 2


def test_reconnects_and_retries_once_on_a_garbled_response_from_a_kept_connection(
    server, client
):
    garbled = {"raw": b"garbage\r\n\r\n", "after": "close"}
    replies(server, {"status": 200}, garbled)
    client.submit_samples("[]")

    assert client.submit_samples("[]").ok()
    assert server.accepted == 2
    assert len(server.requests) == 3


def test_reconnects_and_retries_once_on_a_body_cut_short_on_a_kept_connection(
    server, client
):
    cut = b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc"
    replies(server, {"status": 200}, {"raw": cut, "after": "close"})
    client.submit_samples("[]")

    assert client.submit_samples("[]").ok()
    assert server.accepted == 2
    assert len(server.requests) == 3


def test_does_not_retry_a_failure_on_a_new_connection(server, client):
    server.on_accept = "reset"

    with pytest.raises(RequestError) as failure:
        client.submit_samples("[]")

    assert "Network error" in str(failure.value)
    assert server.accepted == 1


def test_retries_a_kept_connection_only_once(server, client):
    replies(server, {"status": 200, "after": "close"})
    client.submit_samples("[]")
    server.on_accept = "reset"

    with pytest.raises(RequestError):
        client.submit_samples("[]")

    assert server.accepted == 2


def test_a_retry_shares_the_timeout_of_its_request(server):
    replies(server, {"status": 200, "after": "close"}, {"then": "stall"})
    client = Client(HireFire.configuration, timeout=0.3)
    client.submit_samples("[]")

    elapsed = seconds_until_failure(
        lambda: client.submit_samples("[]"), "Request timed out."
    )

    assert 0.3 <= elapsed < 2


def test_opens_a_fresh_connection_in_a_forked_child(server, client):
    client.submit_samples("[]")
    inherited = client._connection

    client._owner_pid = os.getpid() - 1

    client.submit_samples("[]")

    assert client._connection is not inherited
    assert client._owner_pid == os.getpid()
    assert server.accepted == 2


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
    assert Client(HireFire.configuration)._base_url() == "https://data.hirefire.io"
    monkeypatch.setenv("HIREFIRE_DATA_URL", "///")
    assert Client(HireFire.configuration)._base_url() == "https://data.hirefire.io"
    monkeypatch.setenv("HIREFIRE_DATA_URL", " https://example.test/path/ ")
    assert Client(HireFire.configuration)._base_url() == "https://example.test/path"


@mocketize
def test_custom_data_url(set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "https://custom.hirefire.io")
    Entry.single_register(
        Entry.POST, "https://custom.hirefire.io/metrics/ingest", status=200
    )

    Client(HireFire.configuration).submit_samples(PAYLOAD)

    assert Mocket.last_request().headers.get("host") == "custom.hirefire.io"


@mocketize
def test_custom_data_url_over_plain_http(set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "http://localhost:9999")
    Entry.single_register(
        Entry.POST, "http://localhost:9999/metrics/ingest", status=200
    )

    Client(HireFire.configuration).submit_samples(PAYLOAD)

    assert Mocket.last_request().method == "POST"


@mocketize
def test_custom_data_url_with_a_trailing_slash_does_not_double_the_path(
    set_HIREFIRE_TOKEN, monkeypatch
):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "https://custom.hirefire.io/prefix/")
    Entry.single_register(
        Entry.POST, "https://custom.hirefire.io/prefix/metrics/ingest", status=200
    )

    Client(HireFire.configuration).submit_samples(PAYLOAD)

    assert Mocket.last_request().path == "/prefix/metrics/ingest"


@mocketize
def test_custom_data_url_honors_a_path_prefix(set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("HIREFIRE_DATA_URL", "https://custom.hirefire.io/prefix")
    Entry.single_register(
        Entry.POST, "https://custom.hirefire.io/prefix/metrics/ingest", status=200
    )

    Client(HireFire.configuration).submit_samples(PAYLOAD)

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


def test_reconnects_when_the_kept_connection_sat_idle_past_the_keep_alive_timeout(
    server, client
):
    client.submit_samples("[]")
    client._last_used_at -= Client.KEEP_ALIVE_TIMEOUT + 1

    client.submit_samples("[]")

    assert server.accepted == 2


def test_reuses_a_kept_connection_that_is_still_within_the_keep_alive_timeout(
    server, client
):
    client.submit_samples("[]")
    client._last_used_at -= Client.KEEP_ALIVE_TIMEOUT - 1

    client.submit_samples("[]")

    assert server.accepted == 1


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
