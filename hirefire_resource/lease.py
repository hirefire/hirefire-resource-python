import time
import uuid

from hirefire_resource.client import Client, RequestError


class Lease:
    def __init__(self, enabled=True):
        self._enabled = enabled
        self.process_id = str(uuid.uuid4())
        self._client = Client()
        self._ttl = 15
        self._granted = False
        self._expires_at = time.time()
        self._next_sample_at = time.time()
        self.sample_frequency = 15

    def granted(self):
        return self._granted

    # Advances before yielding so a raising sampler costs one sample window
    # instead of being retried on every dispatcher tick.
    def sample_if_due(self, sampler):
        if not (self._granted and time.time() >= self._next_sample_at):
            return

        self._next_sample_at = time.time() + self.sample_frequency
        sampler()

    # Advances before the request so a failed renewal waits a full TTL instead
    # of blocking the dispatcher thread on every tick.
    def request_if_due(self):
        if not (self._enabled and time.time() >= self._expires_at):
            return

        self._expires_at = time.time() + self._ttl

        try:
            response = self._client.request_lease(self.process_id)
        except Exception:
            # Unconfirmed leases may be re-granted to another process meanwhile;
            # stop sampling until a successful renewal rather than risk two
            # processes sampling the same fleet.
            self._granted = False
            raise

        if response.status == 401:
            self._granted = False
            return

        if not (200 <= response.status < 300):
            self._granted = False
            raise RequestError(f"Lease request failed with {response.status} status.")

        if "HireFire-Sample-Frequency" in response.headers:
            self.sample_frequency = int(
                response.headers.get("HireFire-Sample-Frequency")
            )

        if "HireFire-Lease-TTL" in response.headers:
            self._ttl = int(response.headers.get("HireFire-Lease-TTL"))
            self._expires_at = time.time() + self._ttl

        self._granted = response.headers.get("HireFire-Lease-Granted") == "true"
