import time

from hirefire_resource.cpu.usage import Usage


class CPU:
    def __init__(self, name: str) -> None:
        self.name = str(name)
        self._last_usage: float | None = None
        self._last_time: float | None = None
        self._last_source: str | None = None

    def sample(self) -> None:
        now = time.time()
        usage, source = Usage.reading()

        previous_usage = self._last_usage
        previous_time = self._last_time
        previous_source = self._last_source
        self._last_usage = usage
        self._last_time = now
        self._last_source = source

        if (
            usage is None
            or previous_usage is None
            or previous_time is None
            or source != previous_source
        ):
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
