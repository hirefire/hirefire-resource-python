from contextlib import contextmanager

from hirefire_resource.configuration import Configuration


class HireFire:
    configuration = Configuration()

    @classmethod
    @contextmanager
    def configure(cls):
        yield cls.configuration
