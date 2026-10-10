import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request

SERVER = "http://127.0.0.1:9100"
APP = "http://127.0.0.1:8000/"
BROKER = "redis://127.0.0.1:6379/0"
GUNICORN = ["gunicorn", "-w", "2", "-b", "127.0.0.1:8000", "app_wsgi:application"]
UWSGI = [
    "uwsgi",
    "--master",
    "--processes",
    "2",
    "--http-socket",
    "127.0.0.1:8000",
    "--module",
    "app_wsgi:application",
    "--die-on-term",
]
HOSTS = [
    ("gunicorn sync", GUNICORN, "web"),
    ("gunicorn sync --preload", GUNICORN + ["--preload"], "web"),
    ("gunicorn gthread", GUNICORN + ["-k", "gthread", "--threads", "2"], "web"),
    (
        "gunicorn gthread --preload",
        GUNICORN + ["-k", "gthread", "--threads", "2", "--preload"],
        "web",
    ),
    ("gunicorn gevent", GUNICORN + ["-k", "gevent"], "web"),
    ("gunicorn gevent --preload", GUNICORN + ["-k", "gevent", "--preload"], "web"),
    (
        "uvicorn --workers 2",
        [
            "uvicorn",
            "app_asgi:app",
            "--workers",
            "2",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "warning",
        ],
        "web",
    ),
    ("uwsgi", UWSGI, "web"),
    ("uwsgi --enable-threads", UWSGI + ["--enable-threads"], "web"),
    ("uwsgi --lazy-apps", UWSGI + ["--lazy-apps"], "web"),
    (
        "uwsgi --enable-threads --lazy-apps",
        UWSGI + ["--enable-threads", "--lazy-apps"],
        "web",
    ),
    ("uwsgi --py-call-uwsgi-fork-hooks", UWSGI + ["--py-call-uwsgi-fork-hooks"], "web"),
    (
        "celery prefork",
        [
            "celery",
            "-A",
            "tasks",
            "worker",
            "--pool",
            "prefork",
            "--concurrency",
            "2",
            "--loglevel",
            "warning",
            "--without-gossip",
            "--without-mingle",
        ],
        "worker",
    ),
]
DISPATCH = re.compile(r"HF pid=(\d+) \[HireFire\] Dispatching metrics: (.*)")
STARTED = re.compile(r"HF pid=(\d+) \[HireFire\] Starting dispatcher")
STOPPED = re.compile(r"HF pid=(\d+) \[HireFire\] Dispatcher stopped")
BOOT = re.compile(r"BOOT pid=(\d+) ppid=(\d+)")
THREADS = re.compile(r"threads support", re.I)


def post(path, body=b""):
    request = urllib.request.Request(SERVER + path, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read()


def stats():
    return json.loads(post("/__stats__"))


def hit(count):
    answered = 0
    for _ in range(count):
        request = urllib.request.Request(
            APP, headers={"X-Request-Start": f"t={time.time() - 0.01}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                response.read()
                answered += 1
        except Exception:
            pass
        time.sleep(0.05)
    return answered


def children(pid):
    output = subprocess.run(
        ["ps", "-o", "pid=", "--ppid", str(pid)], capture_output=True, text=True
    ).stdout
    return [int(value) for value in output.split()]


def read(path):
    with open(path, errors="replace") as file:
        return file.read()


def digest(log, master):
    per_pid = {}
    for match in DISPATCH.finditer(log):
        entry = per_pid.setdefault(match.group(1), {"dispatches": 0, "series": set()})
        entry["dispatches"] += 1
        for series in ("rqt", "cpu", "jqs", "jql", "wrk"):
            if f'"{series}"' in match.group(2):
                entry["series"].add(series)
    result = {}
    for pid, entry in per_pid.items():
        role = "master" if int(pid) == master else "worker"
        result[f"{role} {pid}"] = {
            "dispatches": entry["dispatches"],
            "series": sorted(entry["series"]),
        }
    return result


def run(name, command, role):
    log_path = f"/results/{name.replace(' ', '_').replace('--', '')}.log"
    post("/__mode__", b"celery_plan" if role == "worker" else b"healthy")
    before = stats()
    environment = dict(
        os.environ,
        HIREFIRE_TOKEN="host-token",
        HIREFIRE_DATA_URL=SERVER,
        HIREFIRE_VERBOSE="1",
        DYNO=f"{role}.1",
        PYTHONWARNINGS="default",
        PYTHONUNBUFFERED="1",
        CELERY_BROKER_URL=BROKER,
    )
    with open(log_path, "w") as log:
        process = subprocess.Popen(
            command, env=environment, stdout=log, stderr=subprocess.STDOUT
        )
    time.sleep(6 if role == "web" else 12)
    boot_log = read(log_path)
    workers = children(process.pid)
    result = {
        "host": name,
        "command": " ".join(command),
        "boot_warnings": sorted(
            {
                re.sub(r"pid=\d+", "pid=N", line.strip())[:170]
                for line in boot_log.splitlines()
                if "Warning" in line
                or "multi-threaded" in line
                or "threads support" in line
            }
        ),
        "processes_that_booted": sorted(
            f"{'master' if int(pid) == process.pid else 'worker'}"
            for pid, _ppid in BOOT.findall(boot_log)
        ),
        "sent_before_first_request": digest(boot_log, process.pid),
        "server_requests_before_first_request": {
            "ingest": stats()["ingest"] - before["ingest"],
            "lease": stats()["lease"] - before["lease"],
        },
    }
    if role == "web":
        result["requests_answered"] = hit(20)
    else:
        subprocess.run(
            [
                sys.executable,
                "-c",
                f"from celery import Celery; app = Celery(broker={BROKER!r}); [app.send_task('tasks.work', args=[0.3]) for _ in range(8)]",
            ],
            env={**os.environ, "PYTHONWARNINGS": "ignore"},
            capture_output=True,
        )
    time.sleep(5)
    first_log = read(log_path)
    result["senders_after_requests"] = digest(first_log, process.pid)

    booted = [int(pid) for pid, _ppid in BOOT.findall(boot_log) if int(pid) in workers]
    victim = booted[0] if booted else (workers[0] if workers else None)
    restart = {"killed_worker": bool(victim)}
    if victim:
        mark = len(first_log)
        os.kill(
            victim,
            signal.SIGKILL if role == "worker" or "uwsgi" in name else signal.SIGTERM,
        )
        time.sleep(4)
        new_workers = [pid for pid in children(process.pid) if pid not in workers]
        restart["new_workers"] = len(new_workers)
        if role == "web":
            restart["requests_answered"] = hit(20)
        time.sleep(5)
        after_log = read(log_path)[mark:]
        senders = digest(after_log, process.pid)
        restart["senders_after_restart"] = senders
        restart["new_worker_started_dispatcher"] = any(
            int(pid) in new_workers for pid in STARTED.findall(after_log)
        )
        restart["new_worker_sends"] = any(
            int(key.split()[1]) in new_workers for key in senders
        )
        restart["master_events"] = sorted(
            {
                re.sub(r"pid=\d+", "pid=N", line.strip())[:120]
                for line in after_log.splitlines()
                if f"pid={process.pid} " in line and "Dispatching" not in line
            }
        )
    result["worker_restart"] = restart
    if "uwsgi" in name:
        time.sleep(11)
        result["requests_answered_after_11_idle_seconds"] = hit(2)

    stop_started = time.monotonic()
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        result["killed_after_20s"] = True
    result["shutdown_seconds"] = round(time.monotonic() - stop_started, 1)
    subprocess.run(["pkill", "-9", "-f", command[0]])
    final_log = read(log_path)
    result["thread_support_lines"] = sorted(
        {line.strip() for line in final_log.splitlines() if THREADS.search(line)}
    )
    result["uwsgi_warnings"] = sorted(
        {
            re.sub(r"pid=\d+", "pid=N", line.strip())[:150]
            for line in final_log.splitlines()
            if "[HireFire]" in line and "uWSGI" in line
        }
    )
    result["dispatcher_starts"] = len(STARTED.findall(final_log))
    result["dispatcher_stops"] = len(STOPPED.findall(final_log))
    result["error_lines"] = sorted(
        {
            re.sub(
                r"pid=\d+", "pid=N", re.sub(r"\d{4}-\d\d-\d\d[ T][\d:,.]+", "", line)
            ).strip()[:170]
            for line in final_log.splitlines()
            if ("Traceback" in line or "Error" in line or "error" in line)
            and "Dispatching" not in line
        }
    )[:8]
    time.sleep(1)
    return result


def main():
    subprocess.Popen(
        ["redis-server", "--save", "", "--appendonly", "no"], stdout=subprocess.DEVNULL
    )
    server = subprocess.Popen(
        [sys.executable, "fake_server.py", "9100"], stdout=subprocess.DEVNULL
    )
    time.sleep(1.5)
    only = os.environ.get("HOSTS_ONLY")
    results = []
    for name, command, role in HOSTS:
        if only and only not in name:
            continue
        try:
            outcome = run(name, command, role)
        except Exception as error:
            outcome = {
                "host": name,
                "harness_error": f"{type(error).__name__}: {error}",
            }
        results.append(outcome)
        print(json.dumps(outcome), flush=True)
        with open("/results/hosts.json", "w") as file:
            json.dump(results, file, indent=2)
    versions = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=freeze"],
        capture_output=True,
        text=True,
    ).stdout
    wanted = (
        "gunicorn",
        "gevent",
        "uvicorn",
        "uWSGI",
        "celery",
        "Flask",
        "starlette",
        "redis",
        "kombu",
        "billiard",
    )
    with open("/results/versions.txt", "w") as file:
        file.write(f"python {sys.version.split()[0]}\n")
        file.write(
            "".join(
                line + "\n"
                for line in versions.splitlines()
                if line.split("==")[0] in wanted
            )
        )
    server.terminate()


if __name__ == "__main__":
    main()
