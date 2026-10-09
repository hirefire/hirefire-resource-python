import json
import resource
import sys
import sysconfig
import threading
import time

from evidence_support import boot


def contention():
    from hirefire_resource.buffer import Buffer

    def run(threads, calls):
        buffer = Buffer()
        barrier = threading.Barrier(threads + 1)

        def work():
            barrier.wait()
            for _ in range(calls):
                buffer.sample("web", "rqt", 12)

        workers = [threading.Thread(target=work) for _ in range(threads)]
        for worker in workers:
            worker.start()
        barrier.wait()
        started = time.perf_counter()
        for worker in workers:
            worker.join()
        return round((time.perf_counter() - started) / (threads * calls) * 1e9)

    return {
        "gil_disabled": bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
        and not sys._is_gil_enabled(),
        "python": sys.version.split()[0],
        "ns_per_sample_1_thread": run(1, 200_000),
        "ns_per_sample_4_threads": run(4, 100_000),
        "ns_per_sample_8_threads": run(8, 50_000),
    }


def idle():
    hirefire, server, lines = boot(
        env={"DYNO": sys.argv[2] if len(sys.argv) > 2 else "worker.1"}
    )
    time.sleep(2)
    before = resource.getrusage(resource.RUSAGE_SELF)
    cpu_before = time.process_time()
    seconds = 20
    time.sleep(seconds)
    after = resource.getrusage(resource.RUSAGE_SELF)
    result = {
        "dyno": sys.argv[2] if len(sys.argv) > 2 else "worker.1",
        "threads": threading.active_count() - server.live_threads,
        "cpu_ms_per_second": round(
            (time.process_time() - cpu_before) * 1000 / seconds, 3
        ),
        "voluntary_context_switches_per_second": round(
            (after.ru_nvcsw - before.ru_nvcsw) / seconds, 1
        ),
        "requests_per_second": round(len(server.requests) / (seconds + 2), 2),
    }
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    return result


if __name__ == "__main__":
    print(json.dumps(globals()[sys.argv[1]]()))
