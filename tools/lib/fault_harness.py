import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_server import SERVER_FAULTS, FakeServer, healthy  # noqa: E402


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))


class FaultHarness:
    def __init__(self, scenario, fault_seconds, heal_seconds):
        self.scenario = scenario
        self.fault_seconds = fault_seconds
        self.heal_seconds = heal_seconds
        self.capture = Capture()
        self.escaped = []
        self.durations = []
        self.host_running = True

    def run(self):
        server = FakeServer(SERVER_FAULTS.get(self.scenario, healthy))
        os.environ["HIREFIRE_TOKEN"] = "fault-token"
        os.environ["HIREFIRE_DATA_URL"] = self.data_url(server)
        os.environ["DYNO"] = "web.1"
        from hirefire_resource import HireFire

        logger = logging.getLogger("tools.faults")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.handlers = [self.capture]

        def configure():
            with HireFire.configure() as config:
                config.logger = logger
                config.dyno("worker", lambda: 7)

        self.guard("configure", configure)
        host = threading.Thread(target=self.host_loop, daemon=True)
        host.start()
        time.sleep(1)
        start = self.snapshot(server)

        time.sleep(self.fault_seconds)
        fault = self.snapshot(server)
        fault_log = list(self.capture.lines)
        fault_requests = server.requests
        running_after_fault = self.guard(
            "running", lambda: HireFire.configuration.dispatcher.running()
        )

        healed_after = None
        if self.scenario in SERVER_FAULTS:
            server.set_handler(healthy)
            healed_at = time.monotonic()
            deadline = healed_at + self.heal_seconds
            while True:
                first = next(
                    (
                        request
                        for request in server.requests
                        if request.at > healed_at and request.path == "/metrics/ingest"
                    ),
                    None,
                )
                if first is not None:
                    healed_after = round(first.at - healed_at, 2)
                    break
                if time.monotonic() > deadline:
                    break
                time.sleep(0.05)
            time.sleep(1)
            server.set_handler(SERVER_FAULTS[self.scenario])
            time.sleep(3)

        stop_started = time.monotonic()
        self.guard("stop", lambda: HireFire.configuration.stop_dispatcher())
        stop_seconds = round(time.monotonic() - stop_started, 2)

        self.host_running = False
        host.join(2)
        finish = self.snapshot(server)
        server.stop()
        return self.result(
            start,
            fault,
            finish,
            fault_log,
            fault_requests,
            running_after_fault,
            healed_after,
            stop_seconds,
        )

    def data_url(self, server):
        if self.scenario == "refused":
            closed = socket.socket()
            closed.bind(("127.0.0.1", 0))
            port = closed.getsockname()[1]
            closed.close()
            return f"http://127.0.0.1:{port}"
        if self.scenario == "unresolvable":
            return "http://nonexistent-host.invalid"
        if self.scenario == "tls_to_plain":
            return f"https://127.0.0.1:{server.port}"
        return server.url

    def host_loop(self):
        from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware

        app = HireFireMiddleware(lambda environ, start_response: [])
        while self.host_running:
            environ = {"HTTP_X_REQUEST_START": f"t={time.time() - 0.02}"}
            started = time.monotonic()
            self.guard("middleware", lambda: app(environ, None))
            self.durations.append(time.monotonic() - started)
            time.sleep(0.005)

    def guard(self, where, function):
        try:
            return function()
        except BaseException as error:
            self.escaped.append(f"{where}: {type(error).__name__}: {error}")
            return None

    def snapshot(self, server):
        rss = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True
        ).stdout.strip()
        return {
            "at": time.monotonic(),
            "rss_kb": int(rss or 0),
            "threads": threading.active_count() - server.live_threads,
            "accepted": server.accepted,
            "open_sockets": server.open_sockets,
            "requests": len(server.requests),
            "log_lines": len(self.capture.lines),
        }

    def result(
        self,
        start,
        fault,
        finish,
        fault_log,
        fault_requests,
        running_after_fault,
        healed_after,
        stop_seconds,
    ):
        seconds = fault["at"] - start["at"]
        durations = sorted(self.durations)
        messages = {}
        for _level, message in fault_log:
            text = re.sub(r"\d+(\.\d+)?", "N", message).strip()[:110]
            messages[text] = messages.get(text, 0) + 1
        levels = {}
        for level, _message in fault_log:
            levels[level] = levels.get(level, 0) + 1
        top = sorted(messages.items(), key=lambda item: -item[1])[:4]
        return {
            "scenario": self.scenario,
            "fault_seconds": round(seconds, 1),
            "escaped": self.escaped,
            "middleware_calls": len(durations),
            "middleware_p99_ms": round(durations[int(len(durations) * 0.99)] * 1000, 3),
            "middleware_max_ms": round(durations[-1] * 1000, 3),
            "ingest_requests": sum(
                1 for r in fault_requests if r.path == "/metrics/ingest"
            ),
            "lease_requests": sum(
                1 for r in fault_requests if r.path == "/metrics/lease"
            ),
            "connections_accepted": fault["accepted"],
            "log_lines": len(fault_log),
            "log_lines_per_minute": round(len(fault_log) * 60 / seconds),
            "log_levels": levels,
            "top_messages": [f"{count}x {message}" for message, count in top],
            "dispatcher_running_after_fault": running_after_fault,
            "threads_start": start["threads"],
            "threads_after_fault": fault["threads"],
            "threads_after_stop": finish["threads"],
            "rss_start_kb": start["rss_kb"],
            "rss_after_fault_kb": fault["rss_kb"],
            "recovered_after_seconds": healed_after,
            "stop_seconds_under_fault": stop_seconds,
        }


if __name__ == "__main__":
    scenario, path, fault_seconds, heal_seconds = sys.argv[1:5]
    outcome = FaultHarness(scenario, int(fault_seconds), int(heal_seconds)).run()
    Path(path).write_text(json.dumps(outcome, indent=2) + "\n")
