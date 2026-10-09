import gc
import json
import os
import stat
import subprocess
import sys
import threading
import time
import urllib.request

from evidence_support import capture_logger


def socket_fds():
    count = 0
    for name in os.listdir("/dev/fd"):
        try:
            if stat.S_ISSOCK(os.fstat(int(name)).st_mode):
                count += 1
        except (OSError, ValueError):
            pass
    return count


def stats(url):
    with urllib.request.urlopen(url + "/__stats__", data=b"") as response:
        return json.loads(response.read())


def main():
    role = sys.argv[1]
    server = subprocess.Popen(
        [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "..", "lib", "fake_server.py"),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{server.stdout.readline().strip()}"
    os.environ.update(
        {
            "HIREFIRE_TOKEN": "evidence-token",
            "HIREFIRE_DATA_URL": url,
            "DYNO": f"{role}.1",
        }
    )
    from hirefire_resource import HireFire

    logger, lines = capture_logger()
    with HireFire.configure() as config:
        config.logger = logger
        config.dyno("worker", lambda: 1)
    time.sleep(2.5)
    before = stats(url)
    parent_sockets = socket_fds()
    dispatcher = HireFire.configuration.dispatcher
    client_fds = [
        client._connection.sock.fileno()
        for client in (dispatcher._client, dispatcher._lease._client)
        if client._connection is not None and client._connection.sock is not None
    ]

    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_end)
        at_fork = socket_fds()
        gc.collect()
        time.sleep(3)
        still_open = 0
        for fd in client_fds:
            try:
                still_open += stat.S_ISSOCK(os.fstat(fd).st_mode)
            except OSError:
                pass
        report = {
            "socket_fds_right_after_fork": at_fork,
            "inherited_client_sockets": len(client_fds),
            "inherited_client_sockets_still_open_after_3s": int(still_open),
            "dispatcher_running": HireFire.configuration.dispatcher.running(),
            "threads": threading.active_count(),
        }
        os.write(write_end, json.dumps(report).encode())
        os._exit(0)
    os.close(write_end)
    running_after_fork = HireFire.configuration.dispatcher.running()
    time.sleep(3.5)
    child = json.loads(os.read(read_end, 65536).decode())
    os.waitpid(pid, 0)
    after = stats(url)
    result = {
        "process": os.environ["DYNO"],
        "parent_socket_fds_before_fork": parent_sockets,
        "parent_running_right_after_fork": running_after_fork,
        "parent_running_3s_later": HireFire.configuration.dispatcher.running(),
        "child": child,
        "ingest_requests_during_3s_after_fork": after["ingest"] - before["ingest"],
        "connections_accepted_after_fork": after["accepted"] - before["accepted"] - 1,
    }
    HireFire.configuration.stop_dispatcher(flush=False)
    server.terminate()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
