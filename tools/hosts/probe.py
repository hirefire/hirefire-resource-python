import json
import os
import subprocess
import sys
import time

from hirefire_resource.source.cpu.usage import Usage


def main():
    reading, source = Usage.reading()
    result = {
        "usage_source": source,
        "available_cpus": Usage.available_cpus(),
        "os_cpu_count": os.cpu_count(),
        "affinity_count": len(os.sched_getaffinity(0)),
        "nproc": int(subprocess.run(["nproc"], capture_output=True, text=True).stdout),
        "cpu_max": Usage.read(Usage.CGROUP_V2_QUOTA),
    }
    sleepers = [
        subprocess.Popen(["sleep", "30"])
        for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    ]
    started = time.perf_counter()
    for _ in range(50):
        Usage.proc_namespace_seconds()
    result["processes"] = len(os.listdir("/proc")) and sum(
        name.isdigit() for name in os.listdir("/proc")
    )
    result["proc_reading_ms"] = round((time.perf_counter() - started) / 50 * 1000, 3)
    started = time.perf_counter()
    for _ in range(200):
        Usage.reading()
    result["cgroup_reading_ms"] = round((time.perf_counter() - started) / 200 * 1000, 4)
    for sleeper in sleepers:
        sleeper.kill()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
