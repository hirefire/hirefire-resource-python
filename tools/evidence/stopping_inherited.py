import json
import os
import threading
import time

from evidence_support import boot


def main():
    hirefire, server, lines = boot(
        lambda request: {"then": "stall"}, env={"DYNO": "web.1"}
    )
    time.sleep(1.2)
    dispatcher = hirefire.configuration.dispatcher
    stopper = threading.Thread(
        target=lambda: hirefire.configuration.stop_dispatcher(), daemon=True
    )
    stopper.start()
    time.sleep(0.5)
    stopping_at_fork = dispatcher._stopping
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_end)
        results = []
        for _ in range(5):
            results.append(hirefire.configuration.dispatcher.start())
            time.sleep(1)
        report = {
            "start_returns_in_child_over_5s": results,
            "running_in_child": hirefire.configuration.dispatcher.running(),
        }
        os.write(write_end, json.dumps(report).encode())
        os._exit(0)
    os.close(write_end)
    child = json.loads(os.read(read_end, 65536).decode())
    os.waitpid(pid, 0)
    server.stop()
    print(json.dumps({"parent_was_stopping_at_the_fork": stopping_at_fork, **child}))


if __name__ == "__main__":
    main()
