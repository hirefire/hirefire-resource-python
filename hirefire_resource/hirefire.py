from contextlib import contextmanager

from hirefire_resource.configuration import Configuration


class HireFire:
    """
    The `HireFire` class is the main entry point for integrating the `hirefire-resource`
    package into your application. It provides a configuration interface to define how
    HireFire should collect, serve, and dispatch metrics, which are essential for the
    autoscaling decisions made by HireFire for Heroku dynos. Users can configure metrics
    collection for web and worker dynos.

    This setup should be placed in the part of your Python codebase that is executed
    during the application's startup.

    Attributes:
        configuration (Configuration): Holds the current configuration instance for
                                       HireFire metrics collection and dispatching.

    Example:
        from hirefire_resource import HireFire
        from hirefire_resource.macro.rq import job_queue_latency

        with HireFire.configure() as config:
            # To collect Request Queue Time metrics for autoscaling `web` dynos:
            config.dyno("web")
            # To collect Job Queue Latency metrics for autoscaling `worker` dynos:
            config.dyno("worker", lambda: job_queue_latency("default"))
    """

    configuration = None

    @classmethod
    @contextmanager
    def configure(cls):
        """
        A context manager that yields the current configuration to a block, allowing
        for the configuration of the `hirefire-resource` package. This method should be
        called from an application's startup script or similar.

        Yields:
            Configuration: The current or new configuration instance to be modified.
        """
        if cls.configuration is None:
            cls.configuration = Configuration()
        yield cls.configuration
