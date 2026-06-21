import time

from hirefire_resource.cpu.usage import Usage


class CPU:
    def __init__(self, name):
        self.name = str(name)
        self._last_usage = None
        self._last_time = None

    def sample(self):
        now = time.time()
        usage = Usage.total_seconds()

        previous_usage = self._last_usage
        previous_time = self._last_time
        self._last_usage = usage
        self._last_time = now

        if usage is None or previous_usage is None:
            return

        wall_delta = now - previous_time
        usage_delta = usage - previous_usage

        if wall_delta <= 0 or usage_delta < 0:
            return

        available = Usage.available_cpus()
        if available is None or available <= 0:
            return

        cores_used = usage_delta / wall_delta
        percentage = max(0.0, min(100.0, cores_used / available * 100.0))

        from hirefire_resource.hirefire import HireFire

        HireFire.configuration.buffer.sample_cpu(self.name, round(percentage, 2))
