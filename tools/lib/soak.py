import csv
import gc
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PHASES = [
    "healthy",
    "status_500",
    "healthy",
    "stall",
    "healthy",
    "reset",
    "healthy",
    "status_401",
    "healthy",
    "garbage",
    "healthy",
    "lease_denied",
]
PHASE_SECONDS = 20
FORK_EVERY = 15
SAMPLE_EVERY = 10
HOSTS = 4


class Count(logging.Handler):
    def __init__(self):
        super().__init__()
        self.levels = {}
        self.messages = {}

    def emit(self, record):
        self.levels[record.levelname] = self.levels.get(record.levelname, 0) + 1
        text = record.getMessage()[:70]
        self.messages[text] = self.messages.get(text, 0) + 1


class Soak:
    def __init__(self, seconds, directory):
        self.seconds = seconds
        self.directory = Path(directory)
        self.count = Count()
        self.escaped = []
        self.running = True
        self.middleware_max = 0.0
        self.middleware_calls = 0
        self.lock = threading.Lock()
        self.restart_delays = []
        self.phase = "healthy"

    def post(self, path, body=b""):
        request = urllib.request.Request(self.url + path, data=body, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read()

    def seed(self):
        import redis
        import rq

        connection = redis.Redis.from_url(os.environ["TOOLS_REDIS_URL"])
        connection.flushdb()
        default = rq.Queue("default", connection=connection)
        mailers = rq.Queue("mailers", connection=connection)
        for _ in range(200):
            default.enqueue("builtins.print")
        for _ in range(50):
            mailers.enqueue("builtins.print")
        now = time.time()
        for index in range(600):
            name = ("default", "mailers", "other")[index % 3]
            score = now + (-60 if index < 300 else 3600)
            connection.zadd(f"rq:scheduled:{name}", {f"soak-{index}": score})
        connection.close()

    def run(self):
        server = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "fake_server.py"),
                "0",
                "soak",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.url = f"http://127.0.0.1:{server.stdout.readline().strip()}"
        self.seed()
        os.environ["HIREFIRE_TOKEN"] = "soak-token"
        os.environ["HIREFIRE_DATA_URL"] = self.url
        os.environ["HIREFIRE_RQ_URL"] = os.environ["TOOLS_REDIS_URL"]
        os.environ["DYNO"] = "web.1"
        import rq  # noqa: F401

        from hirefire_resource import HireFire

        logger = logging.getLogger("tools.soak")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.handlers = [self.count]
        with HireFire.configure() as config:
            config.logger = logger
            config.dyno("local", lambda: 11)
        hosts = [
            threading.Thread(target=self.host_loop, daemon=True) for _ in range(HOSTS)
        ]
        for host in hosts:
            host.start()
        time.sleep(2)

        rows = []
        started = time.monotonic()
        next_sample = started
        next_fork = started + FORK_EVERY
        next_phase = started
        phase_index = -1
        pids = []
        forks = 0
        while time.monotonic() - started < self.seconds:
            now = time.monotonic()
            if now >= next_phase:
                phase_index += 1
                self.phase = PHASES[phase_index % len(PHASES)]
                self.post("/__mode__", self.phase.encode())
                next_phase += PHASE_SECONDS
            if now >= next_fork:
                pids.append(self.fork_child(HireFire))
                forks += 1
                next_fork += FORK_EVERY
            pids = [pid for pid in pids if os.waitpid(pid, os.WNOHANG)[0] == 0]
            if now >= next_sample:
                rows.append(self.sample(started, len(pids)))
                next_sample += SAMPLE_EVERY
            time.sleep(0.1)

        self.post("/__mode__", b"healthy")
        time.sleep(3)
        rows.append(self.sample(started, len(pids)))
        self.running = False
        for host in hosts:
            host.join(2)
        stop_started = time.monotonic()
        try:
            HireFire.configuration.stop_dispatcher()
        except BaseException as error:
            self.escaped.append(f"stop: {type(error).__name__}: {error}")
        stop_seconds = time.monotonic() - stop_started
        for pid in pids:
            os.waitpid(pid, 0)
        time.sleep(1)
        final = self.sample(started, 0)
        stats = json.loads(self.post("/__stats__"))
        server.terminate()
        self.write(rows)
        return self.summary(rows, final, stats, forks, stop_seconds)

    def fork_child(self, hirefire):
        forked = time.monotonic()
        pid = os.fork()
        if pid == 0:
            try:
                from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware

                app = HireFireMiddleware(lambda environ, start_response: [])
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    app({"HTTP_X_REQUEST_START": f"t={time.time() - 0.01}"}, None)
                    time.sleep(0.05)
            finally:
                os._exit(0)
        deadline = forked + 5
        while time.monotonic() < deadline:
            if hirefire.configuration.dispatcher.running():
                self.restart_delays.append(time.monotonic() - forked)
                break
            time.sleep(0.005)
        else:
            self.restart_delays.append(None)
        return pid

    def host_loop(self):
        from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware

        app = HireFireMiddleware(lambda environ, start_response: [])
        while self.running:
            environ = {"HTTP_X_REQUEST_START": f"t={time.time() - 0.02}"}
            started = time.monotonic()
            try:
                app(environ, None)
            except BaseException as error:
                with self.lock:
                    self.escaped.append(f"middleware: {type(error).__name__}: {error}")
            elapsed = time.monotonic() - started
            with self.lock:
                self.middleware_calls += 1
                self.middleware_max = max(self.middleware_max, elapsed)
            time.sleep(0.01)

    def sample(self, started, children):
        gc.collect()
        rss = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True
        ).stdout.strip()
        stats = json.loads(self.post("/__stats__"))
        return {
            "second": round(time.monotonic() - started),
            "phase": self.phase,
            "rss_kb": int(rss or 0),
            "threads": threading.active_count(),
            "fds": len(os.listdir("/dev/fd")),
            "objects": len(gc.get_objects()),
            "server_open_sockets": stats["open_sockets"],
            "server_accepted": stats["accepted"],
            "children": children,
            "log_lines": sum(self.count.levels.values()),
        }

    def write(self, rows):
        with open(self.directory / "soak.csv", "w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def summary(self, rows, final, stats, forks, stop_seconds):
        half = rows[len(rows) // 2 :]
        minutes = (half[-1]["second"] - half[0]["second"]) / 60 or 1
        delays = [delay for delay in self.restart_delays if delay is not None]
        top = sorted(self.count.messages.items(), key=lambda item: -item[1])[:8]
        return {
            "seconds": rows[-1]["second"],
            "samples": len(rows),
            "phases": PHASES,
            "forks": forks,
            "parent_restarted_after_fork": len(delays),
            "parent_not_restarted_within_5s": len(self.restart_delays) - len(delays),
            "restart_delay_max_ms": round(max(delays) * 1000, 1) if delays else None,
            "escaped": self.escaped,
            "middleware_calls": self.middleware_calls,
            "middleware_max_ms": round(self.middleware_max * 1000, 2),
            "rss_kb": {
                "first": rows[0]["rss_kb"],
                "max": max(r["rss_kb"] for r in rows),
                "last": rows[-1]["rss_kb"],
            },
            "rss_kb_per_minute_second_half": round(
                (half[-1]["rss_kb"] - half[0]["rss_kb"]) / minutes, 1
            ),
            "objects": {
                "first": rows[0]["objects"],
                "max": max(r["objects"] for r in rows),
                "last": rows[-1]["objects"],
            },
            "objects_per_minute_second_half": round(
                (half[-1]["objects"] - half[0]["objects"]) / minutes, 1
            ),
            "threads": {
                "min": min(r["threads"] for r in rows),
                "max": max(r["threads"] for r in rows),
                "after_stop": final["threads"],
            },
            "fds": {
                "min": min(r["fds"] for r in rows),
                "max": max(r["fds"] for r in rows),
                "after_stop": final["fds"],
            },
            "server_open_sockets": {
                "max": max(r["server_open_sockets"] for r in rows),
                "after_stop": final["server_open_sockets"],
            },
            "connections_accepted": stats["accepted"],
            "ingest_requests": stats["ingest"],
            "lease_requests": stats["lease"],
            "log_lines": dict(self.count.levels),
            "top_messages": [f"{count}x {message}" for message, count in top],
            "stop_seconds": round(stop_seconds, 2),
        }


if __name__ == "__main__":
    seconds = int(sys.argv[1])
    directory = sys.argv[2]
    Path(directory).mkdir(parents=True, exist_ok=True)
    outcome = Soak(seconds, directory).run()
    (Path(directory) / "soak.json").write_text(json.dumps(outcome, indent=2) + "\n")
    print(json.dumps(outcome, indent=2))
