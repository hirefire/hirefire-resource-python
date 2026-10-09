import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

SERVER = "http://127.0.0.1:9100"
APP = "http://127.0.0.1:8000/"
GUNICORN = [
    "gunicorn",
    "-w",
    "2",
    "-b",
    "127.0.0.1:8000",
    "app_wsgi:application",
    "--preload",
]
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
HOSTS = {
    "gunicorn sync --preload": GUNICORN,
    "gunicorn gthread --preload": GUNICORN + ["-k", "gthread", "--threads", "2"],
    "gunicorn gevent --preload": GUNICORN + ["-k", "gevent"],
    "uwsgi": UWSGI,
    "uwsgi --py-call-uwsgi-fork-hooks": UWSGI + ["--py-call-uwsgi-fork-hooks"],
    "uwsgi --lazy-apps": UWSGI + ["--lazy-apps"],
}
RUNS = int(os.environ.get("RACE_RUNS", "10"))


def hit(count):
    answered = 0
    for _ in range(count):
        request = urllib.request.Request(
            APP, headers={"X-Request-Start": f"t={time.time() - 0.01}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                answered += response.status == 200
        except Exception:
            pass
    return answered


def once(command, token, index):
    path = f"/tmp/race-{index}.log"
    environment = dict(
        os.environ, HIREFIRE_DATA_URL=SERVER, DYNO="web.1", PYTHONUNBUFFERED="1"
    )
    if token:
        environment["HIREFIRE_TOKEN"] = "host-token"
    else:
        environment.pop("HIREFIRE_TOKEN", None)
    with open(path, "w") as log:
        process = subprocess.Popen(
            command, env=environment, stdout=log, stderr=subprocess.STDOUT
        )
    time.sleep(3)
    answered = hit(10)
    time.sleep(2)
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-9", "-f", command[0]])
        process.wait()
    with open(path, errors="replace") as file:
        log_text = file.read()
    time.sleep(0.5)
    return {
        "answered": answered,
        "idna": log_text.count("unknown encoding: idna"),
        "request_errors": log_text.count("Error handling request"),
        "master_sent": "Starting dispatcher" in log_text,
    }


def main():
    server = subprocess.Popen(
        [sys.executable, "fake_server.py", "9100"], stdout=subprocess.DEVNULL
    )
    time.sleep(1)
    results = []
    for name, command in HOSTS.items():
        for token in (True, False):
            runs = [once(command, token, index) for index in range(RUNS)]
            entry = {
                "host": name,
                "hirefire_token": token,
                "runs": RUNS,
                "runs_with_all_10_requests_answered": sum(
                    run["answered"] == 10 for run in runs
                ),
                "runs_with_no_request_answered": sum(
                    run["answered"] == 0 for run in runs
                ),
                "requests_answered_per_run": [run["answered"] for run in runs],
                "runs_with_idna_error": sum(run["idna"] > 0 for run in runs),
                "host_request_errors": sum(run["request_errors"] for run in runs),
            }
            results.append(entry)
            print(json.dumps(entry), flush=True)
            with open("/results/preload-race.json", "w") as file:
                json.dump(results, file, indent=2)
    server.terminate()


if __name__ == "__main__":
    main()
