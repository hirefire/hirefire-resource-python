from contextlib import contextmanager

from hirefire_resource.configuration import Configuration


class HireFire:
    """
    Represents the main interface for integrating the `hirefire-resource` package into Python
    applications. This class facilitates the configuration of metric collection, dispatching and
    serving, for Heroku dyno autoscaling with HireFire. It supports metrics configuration for both
    web and worker dynos. It is typically instantiated during the application's initialization
    phase.

    Attributes:
        configuration (Configuration): Holds the configuration settings for HireFire. It is an
                                       instance of the `Configuration` class, initialized by
                                       default.

    Examples:
        Setting up HireFire for a web application with specific metrics collection for different
        dyno types:

            from hirefire_resource import HireFire
            from hirefire_resource.macro.rq import job_queue_latency

            with HireFire.configure() as config:
                # Configure metrics collection for `web` dynos
                config.dyno("web")
                # Configure metrics collection for `worker` dynos, using job queue latency
                config.dyno("worker", lambda: job_queue_latency("default"))

    Methods:
        configure: Yields the current configuration instance, allowing for the customization of the
                   `hirefire-resource` package. Typically used within an application's
                   initialization script or setup file.

    """
    configuration = Configuration()

    @classmethod
    @contextmanager
    def configure(cls):
        """
        Provides a way to configure the `hirefire-resource` package. This method yields the current
        configuration instance, enabling the user to modify it as needed. It is designed to be used
        in application initialization or setup scripts.

        If the `configuration` attribute is None, a new `Configuration` instance is created and
        assigned.

        Yields:
            Configuration: The current configuration instance, allowing for modifications to be applied.
        """
        if cls.configuration is None:
            cls.configuration = Configuration()
        yield cls.configuration
