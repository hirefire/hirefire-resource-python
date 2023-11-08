class Worker:
    """
    The Worker class is responsible for measuring job queue metrics for various worker
    libraries and making these metrics available to HireFire's servers.

    This class is initialized with a name and a callable object (proc), which contains
    the metric measuring logic. The name should match the worker dyno designation in
    the Procfile, such as 'worker' or 'mailer'. The proc should be a function that returns
    an integer representing the queue metric, either job queue latency or job queue size,
    which will then be made available to the servers.

    Attributes:
        name (str): The name of the worker, corresponding to the dyno name in the Procfile.
        proc (callable): A callable object that returns an integer representing the queue metric.

    Args:
        name (str): The name of the worker.
        proc (callable): A callable object to measure the job queue metric.
    """

    def __init__(self, name, proc):
        """
        Initializes a new instance of the Worker class.

        Args:
            name (str): The name of the worker.
            proc (callable): A callable object that when called, returns an integer representing
                             the queue metric.
        """
        self.name = name
        self.proc = proc

    def call(self):
        """
        Executes the callable object passed during initialization and returns its result.

        This method should be called to obtain the current queue metric
        (job queue latency or job queue size).

        Returns:
            int: The queue metric result from the executed callable.
        """
        return self.proc()
