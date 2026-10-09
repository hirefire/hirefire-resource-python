import json
import subprocess
import sys
import time

CHILD = r"""
import os, sys, time, warnings
sys.path.insert(0, os.environ["PYTHONPATH"].split(os.pathsep)[1])
from evidence_support import boot
from fake_server import GRANT
mode = sys.argv[1]
handler = (lambda request: {"then": "stall"}) if mode == "stall" else None
hirefire, server, lines = boot(handler, env={"DYNO": "worker.1"}, configure=lambda c: c.dyno("worker", lambda: 1))
time.sleep(1.5)
if mode == "fork":
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
    print("WARNINGS", [f"{w.category.__name__}: {str(w.message)[:90]}" for w in caught])
print("EXITING", time.time())
"""


def run(mode, *flags):
    result = subprocess.run(
        [sys.executable, *flags, "-c", CHILD, mode], capture_output=True, text=True
    )
    ended = time.time()
    exiting = [
        line for line in result.stdout.splitlines() if line.startswith("EXITING")
    ]
    warnings_line = [
        line for line in result.stdout.splitlines() if line.startswith("WARNINGS")
    ]
    return {
        "mode": mode,
        "flags": list(flags),
        "exit_code": result.returncode,
        "seconds_from_last_line_to_exit": round(ended - float(exiting[0].split()[1]), 2)
        if exiting
        else None,
        "warnings_at_fork": warnings_line[0][9:] if warnings_line else None,
        "stderr_tail": result.stderr.strip().splitlines()[-1][:160]
        if result.stderr.strip()
        else None,
    }


if __name__ == "__main__":
    print(json.dumps(run("healthy")))
    print(json.dumps(run("stall")))
    print(json.dumps(run("fork")))
    print(json.dumps(run("fork", "-W", "error")))
