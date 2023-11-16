import re

from hirefire_resource.web import Web
from hirefire_resource.worker import Worker


class InvalidDynoName(Exception):
    """
    Exception raised when an invalid dyno name is provided.

    This error indicates that the provided name does not conform to the Procfile naming restrictions.
    """


class MissingDynoProc(Exception):
    """
    Exception raised when a required callable (proc) is not provided for a worker instance configuration.

    Worker dynos must have a callable that defines how to measure the job queue metric.
    """


class Configuration:
    """
    The `Configuration` class is responsible for managing the configuration settings for the
    `hirefire-resource` package in a Python application. It allows defining settings for collecting
    and dispatching metrics to HireFire's servers. These metrics are used for autoscaling Heroku
    web and worker dynos.

    Attributes:
        web (Web, None): Instance of the `Web` class for managing web dyno metrics, or `None` if not configured.
        workers (list): A list of `Worker` instances, each representing a configured worker dyno with
            its specific metric measurement logic.

    Exceptions:
        InvalidDynoName: Raised when an invalid dyno name is provided, indicating non-conformance with
                         Procfile naming restrictions.
        MissingDynoProc: Raised when a required callable (proc) is not provided for a worker instance configuration.
                         Worker instances must have a callable that defines how to measure the job queue metric.

    Methods:
        dyno: Configures Web and Worker objects by setting up their metrics collection mechanisms.

    Examples:
        Configuring HireFire for web dyno metrics:

            config = Configuration()
            config.dyno("web")

        Configuring HireFire to measure and provide job queue metrics for a worker dyno:

            config = Configuration()
            config.dyno("worker", lambda: some_metric_collection_logic)
    """

    def __init__(self):
        """
        Initializes a new `Configuration` instance with default settings.
        """
        self.web = None
        self.workers = []

    def dyno(self, name, proc=None):
        """
        Configures Web and Worker objects for the HireFire metrics collection. For web dynos, this
        method initializes a `Web` instance. For worker dynos, it requires a callable (proc) that
        defines the metric collection logic.

        Args:
            name (str): The name of the dyno as declared in the Procfile.
            proc (callable, optional): A callable for worker dynos that returns a metric (e.g., job queue latency).
                                       Required for worker dynos.

        Raises:
            InvalidDynoName: If the dyno name is invalid according to Procfile naming restrictions.
            MissingDynoProc: If a required callable (proc) is not provided for a worker dyno.

        Returns:
            Configuration: The instance of `Configuration` for chaining method calls.

        Examples:
            Configuring a web instance:
                config.dyno("web")

            Configuring a worker instance with custom metric collection logic:
                config.dyno("worker", lambda: some_metric_collection_logic)
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
