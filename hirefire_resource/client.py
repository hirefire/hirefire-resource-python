import http.client
import os
import socket
import threading
import time
from dataclasses import dataclass
from email.message import Message
from typing import cast
from urllib.parse import ParseResult, urlparse

from hirefire_resource.version import VERSION


class RequestError(Exception):
    pass


@dataclass
class Response:
    status: int
    # http.client delivers response headers as an HTTPMessage (a Message subclass),
    # read case-insensitively via .get()/in.
    headers: Message


# Symptoms of a keep-alive socket the peer already dropped: the next use resets, the
# pipe breaks, or a reused socket reads a garbled or empty status line. Retry once.
STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    EOFError,
)


class Client:
    # An idle keep-alive socket is likely server-dropped, so reconnect rather than reuse
    # it. 60 outlasts the 30s max dispatch interval.
    KEEP_ALIVE_TIMEOUT = 60

    def __init__(self, timeout: int = 5) -> None:
        self._timeout = timeout
        self._mutex = threading.Lock()
        self._connection: http.client.HTTPConnection | None = None
        self._owner_pid: int | None = None
        self._last_used_at: float | None = None

    def submit_samples(self, body: str) -> "Response | None":
        token = self._require_token()
        response = self._execute(
            "/metrics/ingest",
            body,
            {
                "Content-Type": "application/json",
                "HireFire-Token": token,
                "HireFire-Agent": f"Python-{VERSION}",
            },
        )

        if 200 <= response.status < 300:
            return response
        elif response.status == 401:
            return None
        elif response.status >= 500:
            raise RequestError(f"Server responded with {response.status} status.")
        else:
            raise RequestError(f"Unexpected response code {response.status}.")

    def request_lease(self, process_id: str) -> Response:
        token = self._require_token()
        return self._execute(
            "/metrics/lease",
            "",
            {
                "HireFire-Token": token,
                "HireFire-Agent": f"Python-{VERSION}",
                "HireFire-Process-ID": process_id,
            },
        )

    def close(self) -> None:
        # Takes the same mutex as _execute, so it never closes a socket mid-request.
        with self._mutex:
            self._reset_connection()

    def _reinit_after_fork(self) -> None:
        self._mutex = threading.Lock()
        self._connection = None
        self._owner_pid = None
        self._last_used_at = None

    def _execute(self, endpoint: str, body: str, headers: dict[str, str]) -> Response:
        uri = urlparse(self._base_url())
        path = uri.path.rstrip("/") + endpoint
        encoded_body = body.encode("utf-8")

        with self._mutex:
            while True:
                reused = self._reusable(uri)
                try:
                    connection = self._connection_for(uri)
                    connection.request("POST", path, encoded_body, headers)
                    response = connection.getresponse()
                    response.read()
                    self._last_used_at = time.monotonic()
                    return Response(response.status, response.headers)
                except (socket.timeout, TimeoutError):
                    self._reset_connection()
                    raise RequestError("Request timed out.")
                except (http.client.HTTPException, OSError) as error:
                    self._reset_connection()
                    # Retry once, and only for a reused connection: a cold failure is a
                    # real fault, not staleness. The retry runs cold, so it cannot loop.
                    if reused and isinstance(error, STALE_CONNECTION_ERRORS):
                        continue
                    raise RequestError(
                        f"Network error ({type(error).__name__}: {error})."
                    )

    def _connection_for(self, uri: ParseResult) -> http.client.HTTPConnection:
        if self._reusable(uri):
            return cast(http.client.HTTPConnection, self._connection)

        self._reset_connection()
        host = cast(str, uri.hostname)
        if uri.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                host, uri.port, timeout=self._timeout
            )
        else:
            connection = http.client.HTTPConnection(
                host, uri.port, timeout=self._timeout
            )
        # http.client connects lazily on the first request, which sets .sock. _reusable
        # then treats a live .sock as an open keep-alive connection.
        self._owner_pid = os.getpid()
        self._connection = connection
        return connection

    # Reuse only a live connection this process opened to the same host. The PID check
    # rebuilds after a fork: the child inherits the connection, but its socket is
    # shared with the parent.
    def _reusable(self, uri: ParseResult) -> bool:
        connection = self._connection
        if connection is None or connection.sock is None:
            return False

        if self._last_used_at is None or (
            time.monotonic() - self._last_used_at > self.KEEP_ALIVE_TIMEOUT
        ):
            return False

        default_port = 443 if uri.scheme == "https" else 80
        return (
            self._owner_pid == os.getpid()
            and connection.host == uri.hostname
            and connection.port == (uri.port or default_port)
        )

    def _reset_connection(self) -> None:
        connection = self._connection
        self._connection = None
        self._last_used_at = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _base_url(self) -> str:
        return os.environ.get("HIREFIRE_DATA_URL", "https://data.hirefire.io")

    def _token(self) -> str | None:
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.token

    def _require_token(self) -> str:
        token = self._token()
        if token:
            return token

        raise RequestError(
            "The HIREFIRE_TOKEN environment variable is not set.\n"
            "Set it to your HireFire token to enable metric dispatch."
        )
