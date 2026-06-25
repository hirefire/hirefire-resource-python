import time
import uuid
from collections.abc import Callable

from hirefire_resource.client import Client, RequestError


class Lease:
    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self.process_id = str(uuid.uuid4())
        self._client = Client()
        self._ttl = 15
        self._granted = False
        self._expires_at = time.time()
        self._next_sample_at = time.time()
        self.sample_frequency = 15

    def granted(self) -> bool:
        return self._granted

    def sample_if_due(self, sampler: Callable[[], None]) -> None:
        if not (self._granted and time.time() >= self._next_sample_at):
            return

        self._next_sample_at = time.time() + self.sample_frequency
        sampler()

    def request_if_due(self) -> None:
        if not (self._enabled and time.time() >= self._expires_at):
            return

        self._expires_at = time.time() + self._ttl

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
            self.sample_frequency = int(sample_frequency)

        ttl = response.headers.get("HireFire-Lease-TTL")
        if ttl is not None:
            self._ttl = int(ttl)
            self._expires_at = time.time() + self._ttl

        self._granted = response.headers.get("HireFire-Lease-Granted") == "true"
