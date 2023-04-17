import logging
import sys

from hirefire_resource.web import Web
from hirefire_resource.worker import Worker


class Configuration:
    def __init__(self):
        self.web = None
        self.workers = []
        self.logger = self._init_logger()

    def _init_logger(self):
        logger = logging.getLogger("hirefire_resource")
        logger.setLevel(logging.INFO)

        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)

        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)

        logger.addHandler(handler)

        return logger

    def dyno(self, name, proc=None):
        if name == "web":
            self.web = Web(self)
        else:
            self.workers.append(Worker(name, proc))
