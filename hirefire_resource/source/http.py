class HTTP:
    """HTTP traffic source: samples request queue time into the ``rqt`` wire strategy.

    Attributes:
        name: The process name this source reports under.
    """

    def __init__(self, name: str) -> None:
        self.name = str(name)

    def sample(self, request_queue_time: int) -> None:
        """Records a request queue-time sample (milliseconds) under the ``rqt`` strategy."""
        from hirefire_resource.hirefire import HireFire

        HireFire.configuration.buffer.sample(self.name, "rqt", request_queue_time)
