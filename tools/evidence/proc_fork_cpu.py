import glob
import json
import os
import time

from hirefire_resource.source.cpu.usage import Usage

CGROUP = Usage.CGROUP_V2_USAGE
PHASE_SECONDS = 10
CHILD_CPU_SECONDS = 0.2


def burn(seconds):
    end = time.process_time() + seconds
    while time.process_time() < end:
        pass


def cgroup_seconds():
    Usage.CGROUP_V2_USAGE = CGROUP
    try:
        return Usage.cgroup_v2_seconds()
    finally:
        Usage.CGROUP_V2_USAGE = "/nonexistent"


def with_reaped_children():
    ticks = 0
    for path in glob.glob(Usage.PROC_STAT_GLOB):
        content = Usage.read(path)
        if content is None:
            continue
        fields = content[content.rfind(")") + 1 :].split()
        ticks += sum(int(value) for value in fields[11:15])
    return ticks / Usage.positive_clock_ticks()


def readings():
    client, source = Usage.reading()
    return {
        "client": client,
        "source": source,
        "with_children": with_reaped_children(),
        "cgroup": cgroup_seconds(),
    }


def phase(work):
    first = readings()
    started = time.monotonic()
    previous = first["client"]
    negative = 0
    samples = 0
    forks = 0
    next_sample = started + 1
    while time.monotonic() - started < PHASE_SECONDS:
        forks += work()
        if time.monotonic() >= next_sample:
            next_sample += 1
            current, _ = Usage.reading()
            samples += 1
            negative += current < previous
            previous = current
    elapsed = time.monotonic() - started
    last = readings()
    return {
        "seconds": round(elapsed, 1),
        "forks": forks,
        "source_of_the_client": last["source"],
        "cores_by_the_client": round((last["client"] - first["client"]) / elapsed, 3),
        "cores_with_reaped_children": round(
            (last["with_children"] - first["with_children"]) / elapsed, 3
        ),
        "cores_by_the_cgroup": round((last["cgroup"] - first["cgroup"]) / elapsed, 3),
        "one_second_samples": samples,
        "samples_with_a_negative_delta": negative,
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
    Usage.CGROUP_V2_USAGE = "/nonexistent"
    Usage.CGROUP_V1_USAGE = "/nonexistent"
    print(
        json.dumps(
            {
                "a_job_per_forked_child": phase(job_in_a_child),
                "jobs_in_the_process": phase(job_in_the_process),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
