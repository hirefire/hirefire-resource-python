import time
import uuid
from collections.abc import Callable

from hirefire_resource.client import Client, RequestError


class Lease:
    # Bound server-supplied cadence: a zero or garbled header must not collapse it to a
    # per-tick storm.
    TTL_BOUNDS = (5, 3600)
    SAMPLE_FREQUENCY_BOUNDS = (1, 3600)

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self.process_id = str(uuid.uuid4())
        self._client = Client()
        self._ttl = 15
        self._granted = False
        # Pace off the monotonic clock so a wall-clock step (e.g. NTP) cannot skew renewal.
        self._expires_at = time.monotonic()
        self._next_sample_at = time.monotonic()
        self.sample_frequency = 15

    def granted(self) -> bool:
        return self._granted

    def sample_if_due(self, sampler: Callable[[], None]) -> None:
        if not (self._granted and time.monotonic() >= self._next_sample_at):
            return

        self._next_sample_at = time.monotonic() + self.sample_frequency
        sampler()

    def request_if_due(self) -> None:
        if not (self._enabled and time.monotonic() >= self._expires_at):
            return

        self._expires_at = time.monotonic() + self._ttl

        try:
            response = self._client.request_lease(self.process_id)
        except Exception:
            self._granted = False
            raise

        if response.status == 401:
            self._granted = False
            return

        if not (200 <= response.status < 300):
            self._granted = False
            raise RequestError(f"Lease request failed with {response.status} status.")

        sample_frequency = response.headers.get("HireFire-Sample-Frequency")
        if sample_frequency is not None:
            self.sample_frequency = self._bounded(
                sample_frequency, self.SAMPLE_FREQUENCY_BOUNDS
            )

        ttl = response.headers.get("HireFire-Lease-TTL")
        if ttl is not None:
            self._ttl = self._bounded(ttl, self.TTL_BOUNDS)
            self._expires_at = time.monotonic() + self._ttl

        self._granted = response.headers.get("HireFire-Lease-Granted") == "true"

    def close(self) -> None:
        self._client.close()

    def _reinit_after_fork(self) -> None:
        self.process_id = str(uuid.uuid4())
        self._granted = False
        self._expires_at = time.monotonic()
        self._next_sample_at = time.monotonic()
        self._client._reinit_after_fork()

    @staticmethod
    def _bounded(value: str, bounds: tuple[int, int]) -> int:
        low, high = bounds
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = low
        return max(low, min(parsed, high))
