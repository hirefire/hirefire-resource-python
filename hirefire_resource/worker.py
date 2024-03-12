import re


class InvalidDynoNameError(Exception):
    pass


class MissingDynoProcError(Exception):
    pass


class Worker:
    PROCESS_NAME_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_-]{0,29}$"

    def __init__(self, name, proc=None):
        self._validate(name, proc)
        self.name = name
        self._proc = proc

    def value(self):
        return self._proc()

    def _validate(self, name, proc):
        if not re.match(self.PROCESS_NAME_PATTERN, name or ""):
            raise InvalidDynoNameError(
                f"Invalid name for Worker({name}, proc). "
                "Ensure it matches the Procfile process name (i.e. web, worker)."
            )

        if proc is None:
            raise MissingDynoProcError(
                f"Missing proc for Worker({name}, proc). "
                "Ensure that you provide a proc that returns the job queue metric."
            )
