import json
import os
import platform
import ssl
import subprocess
import sys
import time

RUNS = 20


def one_run(pause):
    os.environ["HIREFIRE_TOKEN"] = "evidence"
    os.environ["HIREFIRE_DATA_URL"] = "https://127.0.0.1:9"
    os.environ["DYNO"] = "web.1"
    from hirefire_resource import HireFire

    HireFire.boot()
    time.sleep(pause)
    pid = os.fork()
    if pid == 0:
        time.sleep(1)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    HireFire.configuration.stop_dispatcher(flush=False)
    signal = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
    print(json.dumps({"signal": signal}))


def main():
    if len(sys.argv) > 1:
        return one_run(float(sys.argv[1]))
    print(
        json.dumps(
            {
                "platform": platform.system(),
                "python": platform.python_version(),
                "openssl": ssl.OPENSSL_VERSION,
            }
        )
    )
    for pause in (0, 1.0):
        signals = {}
        for _ in range(RUNS):
            output = subprocess.run(
                [sys.executable, __file__, str(pause)],
                capture_output=True,
                text=True,
            ).stdout
            signal = json.loads(output.strip().splitlines()[-1])["signal"]
            signals[signal] = signals.get(signal, 0) + 1
        print(
            json.dumps(
                {
                    "seconds_between_boot_and_fork": pause,
                    "runs": RUNS,
                    "child_ran_to_its_exit": signals.pop(None, 0),
                    "child_killed_by_signal": signals,
                }
            )
        )


if __name__ == "__main__":
    main()
