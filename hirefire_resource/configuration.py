import re

from hirefire_resource.web import Web
from hirefire_resource.worker import Worker

class InvalidDynoName(Exception):
    pass

class MissingDynoProc(Exception):
    pass

class Configuration:
    def __init__(self):
        self.web = None
        self.workers = []

    def dyno(self, name, proc=None):
        if str(name) == "web":
            self.web = Web()
        else:
            if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{0,29}$', str(name)):
                raise InvalidDynoName(
                    f"Invalid name for {self.__class__}#dyno({name}). "
                    "Ensure it matches the Procfile process name "
                    "(i.e. web, worker, mailer, low, high, critical, etc)."
                )
            elif proc is None:
                raise MissingDynoProc("proc must be defined for worker dynos")
            else:
                self.workers.append(Worker(name, proc))

        return self
