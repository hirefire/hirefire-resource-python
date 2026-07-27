class Web:
    """HTTP request queue-time collector for a declared http process.

    Attributes:
        name: The process name this collector reports under.
    """

    def __init__(self, name: str) -> None:
        self.name = str(name)

    def sample(self, request_queue_time: int) -> None:
        """Records a request queue-time sample (milliseconds) into the buffer."""
        from hirefire_resource.hirefire import HireFire

        HireFire.configuration.buffer.sample(self.name, "rqt", request_queue_time)
