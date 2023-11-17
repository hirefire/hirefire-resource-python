import re


class InvalidDynoName(Exception):
    pass


class MissingDynoProc(Exception):
    pass


class Worker:
    def __init__(self, name, proc=None):
        self._validate(name, proc)
        self.name = name
        self.proc = proc

    def call(self):
        return self.proc()

    def _validate(self, name, proc):
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,29}$", name):
            raise InvalidDynoName(
                f"Invalid name for Worker({name}, proc). "
                "Ensure it matches the Procfile process name (i.e. web, worker)."
            )

        if proc is None:
            raise MissingDynoProc(
                f"Missing block for Worker({name}, proc). "
                "Ensure that you provide a proc that returns the job queue metric."
            )
