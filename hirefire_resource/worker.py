class Worker:
    """Holds a declared dyno name and a sampler callable that returns the job
    queue metric (latency or size)."""

    def __init__(self, name, sampler):
        self.name = str(name)
        self._sampler = sampler

    def sample(self):
        return self._sampler()
