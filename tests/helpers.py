import datetime
import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hirefire_resource.plan import hooks

HIREFIRE_TOKEN = "d2e39e50-82b1-478e-a457-5a53bfa153a1"
STARTING = "[HireFire] Starting dispatcher."


@pytest.fixture
def set_HIREFIRE_TOKEN(reset_hirefire, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", HIREFIRE_TOKEN)
    return HIREFIRE_TOKEN


def boot_in_a_new_process(configure_logging=""):
    script = (
        "import os, sys\n"
        "from hirefire_resource import HireFire\n"
        f"{configure_logging}\n"
        "HireFire.boot()\n"
        "sys.stdout.flush()\n"
        "sys.stderr.flush()\n"
        "os._exit(0)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "HIREFIRE_TOKEN": HIREFIRE_TOKEN,
            "HIREFIRE_DATA_URL": "http://127.0.0.1:9",
            "DYNO": "worker.1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def missing_public_names(module):
    return [
        f"{module.__name__}.{name}"
        for name in getattr(module, "__all__", ())
        if not hasattr(module, name)
    ]


def at(epoch):
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)


def fake_macro(**overrides):
    macro = SimpleNamespace(
        plan_options=hooks.plan_options,
        plan_connection_options=hooks.plan_connection_options,
        plan_source=hooks.plan_source,
        supports_plan_strategy=hooks.supports_plan_strategy,
        queues_required=hooks.queues_required,
        before_sample_job_queues=hooks.before_sample_job_queues,
        after_sample_job_queues=hooks.after_sample_job_queues,
        attach=hooks.attach,
        reinit_after_fork=hooks.reinit_after_fork,
        release=hooks.release,
    )
    macro.__dict__.update(overrides)
    return macro


def sample_plan(*entries, live=lambda: True):
    from hirefire_resource import HireFire
    from hirefire_resource.sampler import Sampler

    return Sampler(HireFire.configuration).round(list(entries), live)


def fork_and_report(report, seconds=10):
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        signal.alarm(seconds)
        try:
            os.write(write_fd, str(report()).encode())
        except BaseException as error:
            os.write(write_fd, f"raised {error!r}".encode())
        finally:
            os._exit(0)

    os.close(write_fd)
    try:
        output = b""
        while chunk := os.read(read_fd, 4096):
            output += chunk
        os.waitpid(pid, 0)
    finally:
        os.close(read_fd)
    return output.decode()
