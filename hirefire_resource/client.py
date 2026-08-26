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


PAYLOAD_TOO_LARGE = "payload_too_large"
_DEFAULT_BASE_URL = "https://data.hirefire.io"


@dataclass
class Response:
    status: int
    headers: Message
    body: str = ""


STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)


class Client:
    KEEP_ALIVE_TIMEOUT = 60

    def __init__(self, timeout: int = 5) -> None:
        self._timeout = timeout
        self._mutex = threading.Lock()
        self._connection: http.client.HTTPConnection | None = None
        self._owner_pid: int | None = None
        self._last_used_at: float | None = None

    def submit_samples(self, body: str) -> "Response | None | str":
        token = self._require_token()
        response = self._execute(
            "/metrics/ingest",
            body,
            {
                "Content-Type": "application/json",
                "HireFire-Token": token,
                "HireFire-Agent": f"Python-{VERSION}",
            },
            retain_body=False,
        )

        if 200 <= response.status < 300:
            return response
        elif response.status == 401:
            return None
        elif response.status == 413:
            return PAYLOAD_TOO_LARGE
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
            retain_body=True,
        )

    def close(self) -> None:
        with self._mutex:
            self._reset_connection()

    def _reinit_after_fork(self) -> None:
        self._mutex = threading.Lock()
        connection = self._connection
        self._connection = None
        self._owner_pid = None
        self._last_used_at = None
        if connection is not None:
            self._abandon_inherited_connection(connection)

    def _execute(
        self,
        endpoint: str,
        body: str,
        headers: dict[str, str],
        retain_body: bool = False,
    ) -> Response:
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
                    raw = response.read()
                    body_text = raw.decode("utf-8", "replace") if retain_body else ""
                    self._last_used_at = time.monotonic()
                    return Response(response.status, response.headers, body_text)
                except (socket.timeout, TimeoutError):
                    self._reset_connection()
                    raise RequestError("Request timed out.")
                except (http.client.HTTPException, OSError) as error:
                    self._reset_connection()
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
        self._owner_pid = os.getpid()
        self._connection = connection
        return connection

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
        owner_pid = self._owner_pid
        self._connection = None
        self._owner_pid = None
        self._last_used_at = None
        if connection is None:
            return
        if owner_pid != os.getpid():
            self._abandon_inherited_connection(connection)
            return
        try:
            connection.close()
        except OSError:
            pass

    @staticmethod
    def _abandon_inherited_connection(connection: http.client.HTTPConnection) -> None:
        sock = getattr(connection, "sock", None)
        if sock is None:
            return
        detach = getattr(sock, "detach", None)
        if callable(detach):
            try:
                detach()
            except OSError:
                pass
        try:
            connection.sock = None
        except Exception:
            pass

    def _base_url(self) -> str:
        raw = os.environ.get("HIREFIRE_DATA_URL", _DEFAULT_BASE_URL)
        stripped = str(raw).strip().rstrip("/")
        if not stripped:
            return _DEFAULT_BASE_URL
        return stripped

    def _token(self) -> str | None:
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.token

    def _require_token(self) -> str:
        token = self._token()
        if token:
            return token

        raise RequestError(
            "HireFire token is not set.\n"
            "Set HIREFIRE_TOKEN or config.token to enable metric dispatch."
        )
