import logging

from hirefire_resource.web import Web
from hirefire_resource.worker import Worker


class Configuration:
    def __init__(self):
        self.web = None
        self.workers = []
        self.logger = logging.getLogger()

    def dyno(self, name, proc=None):
        if name == "web":
            self.web = Web()
            self.web.configuration = self
        else:
            self.workers.append(Worker(name, proc))
