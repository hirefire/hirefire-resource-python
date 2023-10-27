import re

from hirefire_resource.web import Web
from hirefire_resource.worker import Worker


class InvalidDynoName(Exception):
    """Exception raised for invalid dyno names."""


class MissingDynoProc(Exception):
    """Exception raised when proc is missing for a worker dyno."""


class Configuration:
    """
    Class responsible for handling dyno configurations.

    Attributes:
        web (Web): Configuration for web dynos.
        workers (list): List of worker configurations.
    """

    def __init__(self):
        """
        Initializes a new Configuration instance.
        """
        self.web = None
        self.workers = []

    def dyno(self, name, proc=None):
        """
        Configure a dyno based on its name and associated process.

        If the dyno name is "web", it configures a web dyno.
        Otherwise, it configures a worker dyno and ensures its name and process adhere to the requirements.

        Args:
            name (str): The name of the dyno.
            proc (str, optional): The associated process for worker dynos.

        Returns:
            Configuration: The current Configuration instance for chaining.

        Raises:
            InvalidDynoName: If the provided dyno name is invalid.
            MissingDynoProc: If proc is missing for a worker dyno.

        Examples:
            >>> config = Configuration()
            >>> config.dyno("web")
            >>> config.dyno("worker", lambda: job_queue_latency("default"))
        """
        if str(name) == "web":
            self.web = Web()
        else:
            if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,29}$", str(name)):
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
