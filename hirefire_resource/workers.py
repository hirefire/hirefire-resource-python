import math


class Workers:
    def __init__(self):
        self._workers = []

    def append(self, worker):
        self._workers.append(worker)

    def any(self):
        return len(self._workers) > 0

    def __iter__(self):
        return iter(self._workers)

    def __len__(self):
        return len(self._workers)

    def __getitem__(self, index):
        return self._workers[index]

    # Samplers are user code: isolate failures and validate values per worker.
    def sample(self):
        for worker in self._workers:
            try:
                value = worker.sample()

                if not self._valid_sample(value):
                    self._logger().error(
                        f"[HireFire] The sampler for dyno {worker.name!r} returned "
                        f"{value!r}; expected a non-negative number. Sample dropped."
                    )
                    continue

                self._buffer().sample_worker(worker.name, value)
            except Exception as error:
                self._logger().error(
                    f"[HireFire] The sampler for dyno {worker.name!r} raised "
                    f"{type(error).__name__}: {error}"
                )

    def _valid_sample(self, value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )

    def _buffer(self):
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.buffer

    def _logger(self):
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.logger
