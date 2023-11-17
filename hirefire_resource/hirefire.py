from contextlib import contextmanager

from hirefire_resource.configuration import Configuration


class HireFire:
    configuration = Configuration()

    @classmethod
    @contextmanager
    def configure(cls):
        if cls.configuration is None:
            cls.configuration = Configuration()

        yield cls.configuration
