import glob
import json
import os
import platform
import time

PHASE_SECONDS = 10
CHILD_CPU_SECONDS = 0.2
STAT_GLOB = "/proc/[0-9]*/stat"
CGROUP_FILES = [
    "/sys/fs/cgroup/cpu.stat",
    "/sys/fs/cgroup/cpuacct/cpuacct.usage",
    "/sys/fs/cgroup/cpu.max",
    "/sys/fs/cgroup/cpu/cpu.cfs_quota_us",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
]


def burn(seconds):
    end = time.process_time() + seconds
    while time.process_time() < end:
        pass


def fields_of(path):
    try:
        with open(path) as file:
            content = file.read()
    except OSError:
        return None
    close = content.rfind(")")
    if close == -1:
        return None
    fields = content[close + 1 :].split()
    return fields if len(fields) >= 15 else None


def ticks(last_field):
    total = 0
    for path in glob.glob(STAT_GLOB):
        fields = fields_of(path)
        if fields is not None:
            total += sum(int(value) for value in fields[11:last_field])
    return total / os.sysconf("SC_CLK_TCK")


def cgroup_seconds():
    try:
        with open("/sys/fs/cgroup/cpuacct/cpuacct.usage") as file:
            return int(file.read()) / 1_000_000_000.0
    except (OSError, ValueError):
        pass
    try:
        with open("/sys/fs/cgroup/cpu.stat") as file:
            for line in file:
                if line.startswith("usage_usec"):
                    return int(line.split()[1]) / 1_000_000.0
    except (OSError, ValueError):
        pass
    return None


def readings():
    return {
        "utime_stime": ticks(13),
        "with_cutime_cstime": ticks(15),
        "cgroup": cgroup_seconds(),
    }


def cores(first, last, key, elapsed):
    if first[key] is None or last[key] is None:
        return None
    return round((last[key] - first[key]) / elapsed, 3)


def phase(work):
    first = readings()
    started = time.monotonic()
    forks = 0
    while time.monotonic() - started < PHASE_SECONDS:
        forks += work()
    elapsed = time.monotonic() - started
    last = readings()
    return {
        "seconds": round(elapsed, 1),
        "forks": forks,
        "cores_by_utime_and_stime": cores(first, last, "utime_stime", elapsed),
        "cores_with_cutime_and_cstime": cores(
            first, last, "with_cutime_cstime", elapsed
        ),
        "cores_by_the_cgroup": cores(first, last, "cgroup", elapsed),
    }


def job_in_a_child():
    pid = os.fork()
    if pid == 0:
        burn(CHILD_CPU_SECONDS)
        os._exit(0)
    os.waitpid(pid, 0)
    return 1


def job_in_the_process():
    burn(CHILD_CPU_SECONDS)
    return 0


def main():
    print(
        json.dumps(
            {
                "kernel": platform.release(),
                "python": platform.python_version(),
                "dyno_type": (os.environ.get("DYNO") or "").split(".")[0],
                "readable_cgroup_files": [
                    path for path in CGROUP_FILES if os.access(path, os.R_OK)
                ],
                "processes_in_proc": len(glob.glob(STAT_GLOB)),
                "clock_ticks": os.sysconf("SC_CLK_TCK"),
                "a_job_per_forked_child": phase(job_in_a_child),
                "jobs_in_the_process": phase(job_in_the_process),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
