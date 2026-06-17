import glob
import os
import time


class Usage:
    """Best-effort reads of container CPU usage and the normalization divisor."""

    CGROUP_V2_USAGE = "/sys/fs/cgroup/cpu.stat"
    CGROUP_V1_USAGE = "/sys/fs/cgroup/cpuacct/cpuacct.usage"
    CGROUP_V2_QUOTA = "/sys/fs/cgroup/cpu.max"
    CGROUP_V1_QUOTA = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
    CGROUP_V1_PERIOD = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"
    CEDAR_MEMORY_LIMIT = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    PROC_STAT_GLOB = "/proc/[0-9]*/stat"

    # Cedar shared dynos expose no CPU limit; the memory limit fingerprints the
    # size, which implies the entitlement. Other sizes fall through.
    CEDAR_SHARED_ENTITLEMENTS = {
        536_870_912: 1.0,  # 512 MB: eco / basic / standard-1x
        1_073_741_824: 2.0,  # 1 GB: standard-2x
    }

    # Cumulative whole-container CPU seconds, first available source wins.
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
                parts = line.split()
                usec = cls._number(parts[1]) if len(parts) > 1 else None
                return usec / 1_000_000.0 if usec is not None else None
        return None

    @classmethod
    def cgroup_v1_seconds(cls):
        usage = cls._number(cls.read(cls.CGROUP_V1_USAGE))
        if usage is None:
            return None
        return usage / 1_000_000_000.0

    # Heroku exposes no cpu cgroup; /proc is PID-namespaced to the dyno, so summing
    # every visible process gives whole-dyno CPU.
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

    # utime + stime ticks; parse after the last ")" since comm may contain spaces
    # and parens, which puts utime at index 11 and stime at index 12.
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

    # os.sysconf raises/absent without sysconf; 100 is the universal USER_HZ default.
    @classmethod
    def clock_ticks(cls):
        try:
            return os.sysconf("SC_CLK_TCK")
        except (ValueError, OSError, AttributeError):
            return 100

    @classmethod
    def process_seconds(cls):
        return time.process_time()

    # CPUs to normalize against: the platform's guarantee, not the host core count.
    # First source wins.
    @classmethod
    def available_cpus(cls):
        for source in (
            cls.cgroup_v2_quota,
            cls.cgroup_v1_quota,
            cls.heroku_entitlement,
            cls.render_entitlement,
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
        if not parts or parts[0] == "max":
            return None

        quota = cls._number(parts[0])
        period = cls._number(parts[1]) if len(parts) > 1 else None
        if quota is None or period is None or period <= 0:
            return None
        return quota / period

    @classmethod
    def cgroup_v1_quota(cls):
        quota = cls._number(cls.read(cls.CGROUP_V1_QUOTA))
        period = cls._number(cls.read(cls.CGROUP_V1_PERIOD))
        if quota is None or period is None or quota <= 0 or period <= 0:
            return None
        return quota / period

    # Gated on DYNO: a v1 memory limit says nothing about CPU off Heroku.
    @classmethod
    def heroku_entitlement(cls):
        if not os.environ.get("DYNO"):
            return None

        limit = cls._number(cls.read(cls.CEDAR_MEMORY_LIMIT))
        if limit is None:
            return None
        return cls.CEDAR_SHARED_ENTITLEMENTS.get(int(limit))

    # Render's explicit core count, gated on RENDER so it never fires elsewhere.
    @classmethod
    def render_entitlement(cls):
        if not os.environ.get("RENDER"):
            return None

        count = cls._number(os.environ.get("RENDER_CPU_COUNT"))
        return count if count is not None and count > 0 else None

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

    # None on malformed content, so the caller falls through instead of raising.
    @staticmethod
    def _number(value):
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None
