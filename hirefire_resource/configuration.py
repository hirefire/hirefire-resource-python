from hirefire_resource.web import Web
from hirefire_resource.worker import Worker


class Configuration:
    def __init__(self):
        self.web = None
        self.workers = []

    def dyno(self, name, proc=None):
        if name == "web":
            self.web = Web()
        else:
            self.workers.append(Worker(name, proc))
