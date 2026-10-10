import http.client
import io
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from email.message import Message
from functools import partial
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import ParseResult, urlparse

from hirefire_resource.errors import RequestError
from hirefire_resource.version import VERSION

if TYPE_CHECKING:
    from hirefire_resource.configuration import Configuration

_DEFAULT_BASE_URL = "https://data.hirefire.io"


@dataclass
class Response:
    status: int
    headers: Message
    body: str = ""

    def ok(self) -> bool:
        return 200 <= self.status < 300

    def unauthorized(self) -> bool:
        return self.status == 401

    def too_large(self) -> bool:
        return self.status == 413

    def integer(self, name: str) -> int | None:
        text = str(self.headers.get(name) or "").strip()
        if not (text.isascii() and text.isdigit()):
            return None
        try:
            value = int(text)
        except ValueError:
            return None
        return value if value > 0 else None


class Deadline:
    def __init__(self, seconds: float) -> None:
        self._expires_at = time.monotonic() + seconds

    def apply(self, sock: socket.socket) -> None:
        remaining = self._expires_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("timed out")
        sock.settimeout(remaining)


class DeadlineReader(io.RawIOBase):
    def __init__(self, file: Any, sock: socket.socket, deadline: Deadline) -> None:
        self._file = file
        self._sock = sock
        self._deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        self._deadline.apply(self._sock)
        data = self._file.read1(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def close(self) -> None:
        self._file.close()
        super().close()


class DeadlineResponse(http.client.HTTPResponse):
    def __init__(self, sock: socket.socket, deadline: Deadline, **options: Any) -> None:
        super().__init__(sock, **options)
        self.fp = io.BufferedReader(DeadlineReader(self.fp, sock, deadline))


STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    http.client.IncompleteRead,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)


class Client:
    TIMEOUT = 5
    MAX_BODY_BYTES = 131_072
    SEND_BYTES = 16_384
    KEEP_ALIVE_TIMEOUT = 60

    def __init__(
        self, configuration: "Configuration", timeout: float = TIMEOUT
    ) -> None:
        self._configuration = configuration
        self._timeout = timeout
        self._mutex = threading.Lock()
        self._connection: http.client.HTTPConnection | None = None
        self._owner_pid: int | None = None
        self._last_used_at: float | None = None

    def submit_samples(self, body: str) -> Response:
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

        if response.ok() or response.unauthorized() or response.too_large():
            return response
        raise RequestError(f"Ingest request failed with {response.status} status.")

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

    def _execute(self, endpoint: str, body: str, headers: dict[str, str]) -> Response:
        uri = urlparse(self._base_url())
        path = uri.path.rstrip("/") + endpoint
        payload = body.encode("utf-8")

        with self._mutex:
            try:
                return self._perform(uri, path, payload, headers)
            except TimeoutError:
                self._reset_connection()
                raise RequestError("Request timed out.")
            except RequestError:
                self._reset_connection()
                raise

    def _perform(
        self, uri: ParseResult, path: str, payload: bytes, headers: dict[str, str]
    ) -> Response:
        deadline = Deadline(self._timeout)
        while True:
            reused = self._reusable(uri)
            try:
                connection = self._connection_for(uri, deadline)
                self._send(connection, path, payload, headers, deadline)
                response = connection.getresponse()
                try:
                    body = self._read_body(response)
                finally:
                    response.close()
                self._last_used_at = time.monotonic()
                return Response(
                    response.status, response.headers, body.decode("utf-8", "replace")
                )
            except TimeoutError:
                raise
            except (http.client.HTTPException, OSError) as error:
                self._reset_connection()
                if reused and isinstance(error, STALE_CONNECTION_ERRORS):
                    continue
                raise RequestError(f"Network error ({type(error).__name__}: {error}).")

    def _send(
        self,
        connection: http.client.HTTPConnection,
        path: str,
        payload: bytes,
        headers: dict[str, str],
        deadline: Deadline,
    ) -> None:
        connection.response_class = partial(DeadlineResponse, deadline=deadline)  # type: ignore[assignment]
        connection.putrequest("POST", path)
        connection.putheader("Content-Length", str(len(payload)))
        for name, value in headers.items():
            connection.putheader(name, value)
        deadline.apply(connection.sock)
        connection.endheaders()
        for start in range(0, len(payload), self.SEND_BYTES):
            deadline.apply(connection.sock)
            connection.send(payload[start : start + self.SEND_BYTES])

    def _read_body(self, response: http.client.HTTPResponse) -> bytes:
        announced = response.length or 0
        body = b""
        if announced <= self.MAX_BODY_BYTES:
            body = response.read(self.MAX_BODY_BYTES + 1)
        if announced > self.MAX_BODY_BYTES or len(body) > self.MAX_BODY_BYTES:
            raise RequestError(
                f"Response body exceeded {self.MAX_BODY_BYTES} bytes "
                f"(status {response.status})."
            )
        if response.length:
            raise http.client.IncompleteRead(body, response.length)
        return body

    def _connection_for(
        self, uri: ParseResult, deadline: Deadline
    ) -> http.client.HTTPConnection:
        if self._reusable(uri):
            return cast(http.client.HTTPConnection, self._connection)

        self._reset_connection()
        self._connection = self._open(uri, deadline)
        self._owner_pid = os.getpid()
        return self._connection

    def _open(self, uri: ParseResult, deadline: Deadline) -> http.client.HTTPConnection:
        host = cast(str, uri.hostname)
        context = None
        connection = http.client.HTTPConnection(host, uri.port)
        if uri.scheme == "https":
            context = ssl.create_default_context()
            context.set_alpn_protocols(["http/1.1"])
            connection = http.client.HTTPSConnection(host, uri.port, context=context)
        connection.auto_open = 0

        sock = self._connect(connection.host, connection.port, deadline)
        if context is not None:
            try:
                deadline.apply(sock)
                sock = context.wrap_socket(sock, server_hostname=host)
            except BaseException:
                sock.close()
                raise
        connection.sock = sock
        return connection

    @staticmethod
    def _connect(host: str, port: int, deadline: Deadline) -> socket.socket:
        failure = OSError(f"No address found for {host}.")
        for family, kind, protocol, _name, address in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        ):
            sock = socket.socket(family, kind, protocol)
            try:
                deadline.apply(sock)
                sock.connect(address)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                return sock
            except OSError as error:
                sock.close()
                failure = error
        raise failure

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

    def _require_token(self) -> str:
        token = self._configuration.token
        if token:
            return token

        raise RequestError(
            "HireFire token is not set.\n"
            "Set HIREFIRE_TOKEN or config.token to enable metric dispatch."
        )
