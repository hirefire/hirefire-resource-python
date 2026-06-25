class Web:
    def __init__(self, name: str) -> None:
        self.name = str(name)

    def sample(self, request_queue_time: int) -> None:
        from hirefire_resource.hirefire import HireFire

        HireFire.configuration.buffer.sample_web(request_queue_time)
