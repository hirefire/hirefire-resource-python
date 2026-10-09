import json
import subprocess
import sys
import threading
import time

import pytest

TEST = (
    "tests/hirefire_resource/test_dispatcher.py"
    "::test_stale_generation_cannot_dispatch_after_restart"
)
RUNS = 10


class PauseBeforeStaleDispatch:
    def __init__(self, pause):
        self.pause = pause
        self.by_main = 0
        self.by_loop_thread = 0
        self.in_stale_call = False
        self.message = None

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        from hirefire_resource.client import Client
        from hirefire_resource.dispatcher import Dispatcher

        dispatch, submit = Dispatcher._dispatch, Client.submit_samples
        plugin = self

        def paused_dispatch(dispatcher, generation=None):
            stale = (
                threading.current_thread() is threading.main_thread()
                and generation is not None
                and not dispatcher._loop_active(generation)
            )
            if not stale:
                return dispatch(dispatcher, generation)
            plugin.in_stale_call = True
            try:
                time.sleep(plugin.pause)
                return dispatch(dispatcher, generation)
            finally:
                plugin.in_stale_call = False

        def counted_submit(client, body):
            if plugin.in_stale_call:
                if threading.current_thread() is threading.main_thread():
                    plugin.by_main += 1
                else:
                    plugin.by_loop_thread += 1
            return submit(client, body)

        Dispatcher._dispatch = paused_dispatch
        Client.submit_samples = counted_submit
        try:
            yield
        finally:
            Dispatcher._dispatch = dispatch
            Client.submit_samples = submit

    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.failed:
            self.message = report.longrepr.reprcrash.message.splitlines()[0]


def one_run(pause):
    plugin = PauseBeforeStaleDispatch(pause)
    code = pytest.main(["-q", "-p", "no:cacheprovider", TEST], plugins=[plugin])
    print(
        json.dumps(
            {
                "failed": code != 0,
                "by_main": plugin.by_main,
                "by_loop_thread": plugin.by_loop_thread,
                "message": plugin.message,
            }
        )
    )


def main():
    if len(sys.argv) > 1:
        return one_run(float(sys.argv[1]))
    for pause in (0, 1.5):
        runs = []
        for _ in range(RUNS):
            output = subprocess.run(
                [sys.executable, __file__, str(pause)],
                capture_output=True,
                text=True,
            ).stdout
            runs.append(json.loads(output.strip().splitlines()[-1]))
        print(
            json.dumps(
                {
                    "pause_in_the_stale_dispatch_seconds": pause,
                    "runs": RUNS,
                    "test_failed": sum(run["failed"] for run in runs),
                    "requests_sent_by_the_stale_dispatch": sum(
                        run["by_main"] for run in runs
                    ),
                    "requests_sent_by_a_loop_thread_meanwhile": sum(
                        run["by_loop_thread"] for run in runs
                    ),
                    "failures": sorted(
                        {run["message"] for run in runs if run["message"]}
                    ),
                }
            )
        )


if __name__ == "__main__":
    main()
