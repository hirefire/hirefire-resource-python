import time

from hirefire_resource.cpu.usage import Usage


class CPU:
    """CPU utilization collector for a declared process.

    Attributes:
        name: The process name this collector reports under.
    """

    def __init__(self, name: str) -> None:
        self.name = str(name)
        self._last_usage: float | None = None
        self._last_time: float | None = None
        self._last_source: str | None = None

    def sample(self) -> None:
        """Samples CPU utilization and buffers a percentage when a delta is available.

        The first sample only seeds a baseline. Later samples no-op when the usage
        source changes, elapsed time is non-positive, usage went backwards, or available
        CPUs cannot be determined. A successful sample is clamped to 0-100 and rounded
        to two decimal places.
        """
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

        if elapsed_delta <= 0 or usage_delta < 0:
            return

        available = Usage.available_cpus()
        if available is None or available <= 0:
            return

        cores_used = usage_delta / elapsed_delta
        percentage = max(0.0, min(100.0, cores_used / available * 100.0))

        from hirefire_resource.hirefire import HireFire

        HireFire.configuration.buffer.sample(self.name, "cpu", round(percentage, 2))
