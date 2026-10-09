import json
import select
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field

REASONS = {
    200: "OK",
    302: "Found",
    401: "Unauthorized",
    413: "Payload Too Large",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}

GRANT_HEADERS = {
    "HireFire-Lease-Granted": "true",
    "HireFire-Lease-TTL": "5",
    "HireFire-Sample-Frequency": "1",
}
PLAN = json.dumps({"job_queues": [{"name": "worker", "strategy": "jqs"}]})
GRANT = {"status": 200, "headers": GRANT_HEADERS, "body": PLAN}
HUGE_PLAN = json.dumps(
    {
        "job_queues": [
            {
                "name": f"queue-{index}",
                "strategy": "jqs",
                "adapter": "rq",
                "queues": [f"q{index}"],
            }
            for index in range(4000)
        ]
    }
)


def healthy(request):
    return GRANT if request.path == "/metrics/lease" else {"status": 200}


def lease_only(lease):
    return (
        lambda request: lease if request.path == "/metrics/lease" else {"status": 200}
    )


def ingest_only(ingest):
    return lambda request: GRANT if request.path == "/metrics/lease" else ingest


SERVER_FAULTS = {
    "healthy": healthy,
    "stall": lambda request: {"then": "stall"},
    "reset": lambda request: {"then": "reset"},
    "close": lambda request: {"then": "close"},
    "garbage": lambda request: {
        "raw": b"\x00\xffthis is not http\r\n\r\n",
        "after": "close",
    },
    "drip": lambda request: {"status": 200, "body": "x" * 40, "drip": 1.0},
    "slow_200": lambda request: {**healthy(request), "delay": 3},
    "status_302": lambda request: {
        "status": 302,
        "headers": {"Location": "/elsewhere"},
    },
    "status_401": lambda request: {"status": 401},
    "status_413": ingest_only({"status": 413}),
    "status_429": lambda request: {"status": 429, "headers": {"Retry-After": "30"}},
    "status_500": lambda request: {"status": 500},
    "status_503": lambda request: {"status": 503},
    "lease_bad_json": lease_only(
        {"status": 200, "headers": GRANT_HEADERS, "body": "{not json"}
    ),
    "lease_wrong_shape": lease_only(
        {
            "status": 200,
            "headers": GRANT_HEADERS,
            "body": json.dumps(
                {
                    "job_queues": [
                        1,
                        "two",
                        None,
                        {"name": 5},
                        {"name": "worker", "strategy": ["jqs"]},
                    ]
                }
            ),
        }
    ),
    "lease_huge_body": lease_only(
        {"status": 200, "headers": GRANT_HEADERS, "body": HUGE_PLAN}
    ),
    "lease_bad_headers": lease_only(
        {
            "status": 200,
            "headers": {
                "HireFire-Lease-Granted": "true",
                "HireFire-Lease-TTL": "abc",
                "HireFire-Sample-Frequency": "-5",
            },
            "body": PLAN,
        }
    ),
    "lease_binary_body": lease_only(
        {"status": 200, "headers": GRANT_HEADERS, "body": b"\xff\xfe\x00\x01{"}
    ),
}
CONNECTION_FAULTS = ["refused", "unresolvable", "tls_to_plain"]
SCENARIOS = list(SERVER_FAULTS) + CONNECTION_FAULTS


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
    def __init__(self, handler=None, record=True, port=0):
        self._handler = handler or healthy
        self._record = record
        self._counts = {}
        self._requests = []
        self._sockets = []
        self._workers = []
        self._accepted = 0
        self._closed = False
        self._mutex = threading.Lock()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", port))
        self._server.listen(128)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def requests(self):
        with self._mutex:
            return list(self._requests)

    @property
    def request_counts(self):
        with self._mutex:
            return dict(self._counts)

    @property
    def accepted(self):
        with self._mutex:
            return self._accepted

    @property
    def open_sockets(self):
        with self._mutex:
            return sum(1 for sock in self._sockets if sock.fileno() != -1)

    @property
    def live_threads(self):
        with self._mutex:
            workers = sum(1 for worker in self._workers if worker.is_alive())
        return workers + (1 if self._thread.is_alive() else 0)

    def set_handler(self, handler):
        with self._mutex:
            self._handler = handler

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
                self._workers = [w for w in self._workers if w.is_alive()]
                self._sockets.append(client)
            worker = threading.Thread(target=self._serve, args=(client,), daemon=True)
            with self._mutex:
                self._workers.append(worker)
            worker.start()

    def _serve(self, client):
        reader = client.makefile("rb")
        try:
            while True:
                request = self._read_request(reader)
                if request is None:
                    break
                with self._mutex:
                    self._counts[request.path] = self._counts.get(request.path, 0) + 1
                    if self._record:
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


def control_handler(server, modes):
    state = {"mode": "healthy"}

    def handle(request):
        if request.path == "/__mode__":
            state["mode"] = request.body.strip() or "healthy"
            return {"status": 200, "body": state["mode"]}
        if request.path == "/__stats__":
            counts = server.request_counts
            stats = {
                "mode": state["mode"],
                "accepted": server.accepted,
                "open_sockets": server.open_sockets,
                "live_threads": server.live_threads,
                "ingest": counts.get("/metrics/ingest", 0),
                "lease": counts.get("/metrics/lease", 0),
            }
            return {"status": 200, "body": json.dumps(stats)}
        return modes[state["mode"]](request)

    return handle


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    server = FakeServer(record=False, port=port)
    server.set_handler(control_handler(server, SERVER_FAULTS))
    print(server.port, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
