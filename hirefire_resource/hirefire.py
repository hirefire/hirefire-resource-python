from contextlib import contextmanager

from hirefire_resource.configuration import Configuration


class HireFire:
    configuration = Configuration()

    @classmethod
    @contextmanager
    def configure(cls):
        yield cls.configuration
        if cls.configuration.token:
            cls.configuration.dispatcher.start()

    @classmethod
    def reset(cls):
        if cls.configuration is not None:
            cls.configuration.stop_dispatcher()
        cls.configuration = Configuration()
