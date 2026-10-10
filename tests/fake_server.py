import select
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

REASONS = {
    200: "OK",
    401: "Unauthorized",
    413: "Payload Too Large",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


@dataclass
class Request:
    verb: str
    path: str
    headers: list
    body: str
    at: float = field(default_factory=time.monotonic)

    def header(self, name):
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


class FakeServer:
    def __init__(self, handler=None):
        self._handler = handler or (lambda request: {"status": 200})
        self._requests = []
        self._sockets = []
        self._accepted = 0
        self._closed = False
        self._mutex = threading.Lock()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(128)
        self.port = self._server.getsockname()[1]
        self.on_accept = None
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def address(self):
        return ("127.0.0.1", self.port)

    @property
    def requests(self):
        with self._mutex:
            return list(self._requests)

    @property
    def accepted(self):
        with self._mutex:
            return self._accepted

    @property
    def open_sockets(self):
        with self._mutex:
            return sum(1 for sock in self._sockets if sock.fileno() != -1)

    def paths(self):
        return [request.path for request in self.requests]

    def stop(self):
        self._closed = True
        self._close(self._server)
        with self._mutex:
            sockets = list(self._sockets)
        for sock in sockets:
            self._close(sock)
        self._thread.join(1)

    def _accept_loop(self):
        while not self._closed:
            try:
                client, _address = self._server.accept()
            except OSError:
                return
            with self._mutex:
                self._accepted += 1
                self._sockets = [s for s in self._sockets if s.fileno() != -1]
                self._sockets.append(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client):
        if self.on_accept == "reset":
            self._reset(client)
            self._close(client)
            return
        if self.on_accept == "stall":
            self._stall(client)
            self._close(client)
            return

        reader = client.makefile("rb")
        try:
            while True:
                request = self._read_request(reader)
                if request is None:
                    break
                with self._mutex:
                    self._requests.append(request)
                    handler = self._handler
                if not self._respond(client, handler(request)):
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                reader.close()
            except OSError:
                pass
            self._close(client)

    def _read_request(self, reader):
        line = reader.readline()
        if not line:
            return None
        parts = line.decode("latin-1").split()
        if len(parts) < 2:
            return None
        headers = []
        while True:
            header = reader.readline()
            if not header:
                break
            text = header.decode("latin-1").rstrip("\r\n")
            if not text:
                break
            name, _, value = text.partition(":")
            headers.append([name, value.strip()])
        length = 0
        for name, value in headers:
            if name.lower() == "content-length" and value.isdigit():
                length = int(value)
        body = reader.read(length) if length > 0 else b""
        return Request(parts[0], parts[1], headers, body.decode("utf-8", "replace"))

    def _respond(self, client, action):
        action = action or {"status": 200}
        if action.get("delay"):
            time.sleep(action["delay"])

        then = action.get("then")
        if then == "stall":
            return self._stall(client)
        if then == "reset":
            return self._reset(client)
        if then == "close":
            return False

        data = action.get("raw") or self._format_response(action)
        if action.get("drip"):
            for index in range(len(data)):
                client.sendall(data[index : index + 1])
                time.sleep(action["drip"])
        else:
            client.sendall(data)

        after = action.get("after")
        if after == "close":
            return False
        if after == "reset":
            return self._reset(client)
        if after == "stall":
            return self._stall(client)
        return True

    def _format_response(self, action):
        status = action.get("status", 200)
        body = action.get("body") or b""
        if isinstance(body, str):
            body = body.encode("utf-8")
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        headers.update(action.get("headers") or {})
        head = "".join(f"{name}: {value}\r\n" for name, value in headers.items())
        line = f"HTTP/1.1 {status} {REASONS.get(status, 'Status')}\r\n"
        return (line + head + "\r\n").encode("latin-1") + body

    def _stall(self, client):
        while not self._closed:
            readable, _, _ = select.select([client], [], [], 0.1)
            if not readable:
                continue
            try:
                if not client.recv(1024):
                    break
            except OSError:
                break
        return False

    def _reset(self, client):
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        return False

    @staticmethod
    def _close(sock):
        try:
            sock.close()
        except OSError:
            pass
