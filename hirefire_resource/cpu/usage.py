import glob
import os
import time


class Usage:
    """Reads container-level CPU usage and the CPU normalization divisor, trying
    progressively less precise sources. All reads are best-effort: a missing or
    unreadable file returns None so the caller can fall through."""

    CGROUP_V2_USAGE = "/sys/fs/cgroup/cpu.stat"
    CGROUP_V1_USAGE = "/sys/fs/cgroup/cpuacct/cpuacct.usage"
    CGROUP_V2_QUOTA = "/sys/fs/cgroup/cpu.max"
    CGROUP_V1_QUOTA = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
    CGROUP_V1_PERIOD = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"
    CEDAR_MEMORY_LIMIT = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    PROC_STAT_GLOB = "/proc/[0-9]*/stat"

    # Cedar shared dynos have no CPU limit anywhere, but each size is bound to a
    # fixed memory limit, so the memory limit identifies the size and the size
    # implies the CPU entitlement. Dedicated dynos are deliberately absent: their
    # cpu count is the real core count, so they fall through.
    CEDAR_SHARED_ENTITLEMENTS = {
        536_870_912: 1.0,  # 512 MB: eco / basic / standard-1x
        1_073_741_824: 2.0,  # 1 GB: standard-2x
    }

    # Cumulative CPU time in seconds for the whole dyno/container, from the first
    # available source: cgroup v2, cgroup v1, the /proc PID namespace, or this
    # process's own clock. Heroku exposes no cpu cgroup at all, so /proc carries
    # it there: it is PID-namespaced to the dyno, so summing every visible process
    # gives whole-dyno CPU. The stdlib clock is the dev/macOS last resort and only
    # sees this process.
    @classmethod
    def total_seconds(cls):
        for source in (
            cls.cgroup_v2_seconds,
            cls.cgroup_v1_seconds,
            cls.proc_namespace_seconds,
            cls.process_seconds,
        ):
            value = source()
            if value is not None:
                return value
        return None

    @classmethod
    def cgroup_v2_seconds(cls):
        content = cls.read(cls.CGROUP_V2_USAGE)
        if content is None:
            return None

        for line in content.splitlines():
            if line.startswith("usage_usec"):
                return float(line.split()[-1]) / 1_000_000.0
        return None

    @classmethod
    def cgroup_v1_seconds(cls):
        usage = cls.read(cls.CGROUP_V1_USAGE)
        if usage is None:
            return None
        return float(usage) / 1_000_000_000.0

    @classmethod
    def proc_namespace_seconds(cls):
        paths = glob.glob(cls.PROC_STAT_GLOB)
        if not paths:
            return None

        ticks = 0
        counted = False
        for path in paths:
            content = cls.read(path)
            if content is None:
                continue
            value = cls.stat_ticks(content)
            if value is None:
                continue
            ticks += value
            counted = True

        if not counted:
            return None
        return float(ticks) / cls.clock_ticks()

    # utime + stime (clock ticks) from a /proc/[pid]/stat line. The comm field
    # (2nd) can contain spaces and parens, so parse from after the last ")": the
    # remaining fields put utime at index 11 and stime at index 12.
    @classmethod
    def stat_ticks(cls, content):
        close = content.rfind(")")
        if close == -1:
            return None

        fields = content[close + 1 :].split()
        if len(fields) < 13:
            return None

        try:
            return int(fields[11]) + int(fields[12])
        except ValueError:
            return None

    # os.sysconf raises ValueError on unknown names, and is absent on platforms
    # without sysconf; 100 is the universal USER_HZ default.
    @classmethod
    def clock_ticks(cls):
        try:
            return os.sysconf("SC_CLK_TCK")
        except (ValueError, OSError, AttributeError):
            return 100

    @classmethod
    def process_seconds(cls):
        return time.process_time()

    # Number of CPUs to normalize usage against — the CPU the platform guarantees
    # this container, not the host's core count. Sources, first answer wins: a
    # cgroup quota (platforms with a hard CPU limit), the Cedar shared-dyno
    # entitlement (shared dynos burst on an 8-core host, so the core count would
    # understate utilization and invert under contention), or the processor count
    # (dedicated machines, where the host's core count is the container's).
    @classmethod
    def available_cpus(cls):
        for source in (
            cls.cgroup_v2_quota,
            cls.cgroup_v1_quota,
            cls.heroku_entitlement,
            cls.processor_count,
        ):
            value = source()
            if value is not None:
                return value
        return None

    @classmethod
    def cgroup_v2_quota(cls):
        value = cls.read(cls.CGROUP_V2_QUOTA)
        if value is None:
            return None

        parts = value.split()
        if not parts:
            return None

        quota = parts[0]
        if quota == "max":
            return None

        period = float(parts[1]) if len(parts) > 1 else 0.0
        if period > 0:
            return float(quota) / period
        return None

    @classmethod
    def cgroup_v1_quota(cls):
        quota = cls.read(cls.CGROUP_V1_QUOTA)
        period = cls.read(cls.CGROUP_V1_PERIOD)
        if quota is None or period is None:
            return None

        quota = int(quota)
        period = float(period)
        if quota <= 0 or period <= 0:
            return None
        return quota / period

    # Gated on DYNO because elsewhere a v1 memory limit says nothing about CPU.
    # Unrecognized fingerprints (dedicated dynos, future sizes) fall through to
    # the processor count.
    @classmethod
    def heroku_entitlement(cls):
        if not os.environ.get("DYNO"):
            return None

        limit = cls.read(cls.CEDAR_MEMORY_LIMIT)
        if limit is None:
            return None
        return cls.CEDAR_SHARED_ENTITLEMENTS.get(int(limit))

    @classmethod
    def processor_count(cls):
        return os.cpu_count()

    @classmethod
    def read(cls, path):
        try:
            if os.access(path, os.R_OK):
                with open(path) as file:
                    return file.read().strip()
        except OSError:
            return None
        return None
