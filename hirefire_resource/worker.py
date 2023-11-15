class Worker:
    """
    Responsible for measuring job queue metrics.

    This class is used for monitoring job queue metrics, such as latency or size,
    and making these metrics available to HireFire's servers. It is initialized
    with a name and a callable that defines the metric measuring logic.

    Attributes:
        name: A string representing the name of the worker. This should correspond
              to the worker dyno designation in the Procfile, such as 'worker' or 'mailer'.
        proc: A callable that, when executed, returns an integer representing the
              queue metric (latency or size).

    Args:
        name (str): The name of the worker, corresponding to the Procfile's dyno name.
        proc (callable): A callable object that returns an integer representing the queue metric.

    """

    def __init__(self, name, proc):
        """Initializes a new instance of the Worker class."""
        self.name = name
        self.proc = proc

    def call(self):
        """Executes the callable passed during initialization and returns its result.

        The result should be an integer representing the measured queue metric,
        which will be made available to HireFire's servers.

        Returns:
            int: The queue metric result from the executed callable.
        """
        return self.proc()
