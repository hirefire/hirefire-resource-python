import re

from hirefire_resource.web import Web
from hirefire_resource.worker import Worker


class InvalidDynoName(Exception):
    """Exception raised for invalid dyno names."""


class MissingDynoProc(Exception):
    """Exception raised when proc is missing for a worker dyno."""


class Configuration:
    """
    Responsible for handling HireFire configuration within an application. It allows defining
    the collection of metrics for web and worker dynos as required for autoscaling decisions
    by HireFire for Heroku dynos.

    Attributes:
        web (Web or None): Instance of the Web class responsible for collecting and dispatching web metrics to
                           HireFire's servers. It is set when calling `dyno("web")`.
        workers (list of Worker): List of Worker class instances, each configured with a dyno name
                                  and a proc that defines its job queue metric measurement logic.

    Raises:
        InvalidDynoName: Raised if the provided dyno name does not conform to the Procfile naming restrictions.
        MissingDynoProc: Raised if a required proc is not provided for a worker dyno configuration.
    """

    def __init__(self):
        """
        Initializes the Configuration instance, preparing it to handle web and worker dyno configurations.
        """
        self.web = None
        self.workers = []

    def dyno(self, name, proc=None):
        """
        Configures Web and Worker objects.

        The proc is ignored for Web objects, as the Web object as it is not used for collecting
        web metrics.

        The proc is required for Worker objects as it should return the job queue latency or
        job queue size metric.

        Args:
            name (str): The name of the dyno as declared in the Procfile.
            proc (callable, optional): The proc for worker dynos that returns an integer representing
                                       the job queue metric. It must be defined for worker dynos.

        Returns:
            Configuration: The current Configuration instance for method chaining.

        Raises:
            InvalidDynoName: If the dyno name does not match Procfile naming restrictions.
            MissingDynoProc: If a proc is missing for a worker dyno.

        Examples:
            >>> config = Configuration()
            # For the collection and dispatching of request queue time metrics
            >>> config.dyno("web")
            # For the collection and serving of job queue metrics
            >>> config.dyno("worker", lambda: job_queue_latency("default"))
        """
        if name == "web":
            self.web = Web()
        elif re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,29}$", name):
            if proc is None:
                raise MissingDynoProc("proc must be defined for worker dynos")
            self.workers.append(Worker(name, proc))
        else:
            raise InvalidDynoName(
                f"Invalid dyno name: '{name}'. Must conform to Procfile naming restrictions."
            )

        return self
