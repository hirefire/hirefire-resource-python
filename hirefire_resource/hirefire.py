from contextlib import contextmanager

from hirefire_resource.configuration import Configuration


class HireFire:
    """
    The `HireFire` class serves as the main entry point for integrating the `hirefire-resource`
    package into your Python application. It offers a configuration interface for specifying how
    HireFire collects, serves, and dispatches metrics, which are necessary for the autoscaling
    decisions made by dyno managers on HireFire.

    This setup is typically implemented in an initializer within a Django or Flask application.  For
    other Python applications, the configuration should be placed in a part of your codebase that is
    executed during application startup.

    Attributes:
        configuration (Configuration): An instance of the `Configuration` class to hold the HireFire
                                       configuration settings.

    Examples:
        Configuring HireFire to collect metrics for web (i.e., Gunicorn) and worker (i.e., RQ):

            from hirefire_resource import HireFire
            from hirefire_resource.macro.rq import job_queue_latency

            with HireFire.configure() as config:
                # To collect Request Queue Time metrics for autoscaling `web` dynos:
                config.dyno("web")
                # To collect Job Queue Latency metrics for autoscaling `worker` dynos:
                config.dyno("worker", lambda: job_queue_latency("default"))

    Methods:
        configure: A class method that yields the current configuration to a block, allowing for the
                   configuration of the `hirefire-resource` package.
    """

    configuration = Configuration()

    @classmethod
    @contextmanager
    def configure(cls):
        """
        A context manager that yields the current configuration instance, allowing for the
        configuration of the `hirefire-resource` package. This method is typically called from an
        initializer file or any other setup script in your application.

        Yields:
            Configuration: The current configuration instance to be modified.
        """
        if cls.configuration is None:
            cls.configuration = Configuration()
        yield cls.configuration
