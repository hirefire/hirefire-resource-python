import time

from hirefire_resource.cpu.usage import Usage


class CPU:
    def __init__(self, name: str) -> None:
        self.name = str(name)
        self._last_usage: float | None = None
        self._last_time: float | None = None
        self._last_source: str | None = None

    def sample(self) -> None:
        # Measure the interval on the monotonic clock so a wall-clock step (e.g. NTP)
        # cannot distort the utilization delta. The buffered sample's bucket timestamp
        # stays wall-clock.
        now = time.monotonic()
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

        elapsed_delta = now - previous_time
        usage_delta = usage - previous_usage

        # elapsed_delta <= 0 is a backstop: the monotonic clock never steps back.
        if elapsed_delta <= 0 or usage_delta < 0:
            return

        available = Usage.available_cpus()
        if available is None or available <= 0:
            return

        cores_used = usage_delta / elapsed_delta
        percentage = max(0.0, min(100.0, cores_used / available * 100.0))

        from hirefire_resource.hirefire import HireFire

        HireFire.configuration.buffer.sample_cpu(self.name, round(percentage, 2))
