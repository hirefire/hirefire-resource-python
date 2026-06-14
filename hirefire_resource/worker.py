class Worker:
    """A dyno name and a sampler callable returning the job queue metric."""

    def __init__(self, name, sampler):
        self.name = str(name)
        self._sampler = sampler

    def sample(self):
        return self._sampler()
