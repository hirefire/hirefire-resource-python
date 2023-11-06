from contextlib import contextmanager

from hirefire_resource.configuration import Configuration


class Resource:
    configuration = None

    @classmethod
    @contextmanager
    def configure(cls):
        cls.configuration = Configuration()
        yield cls.configuration
