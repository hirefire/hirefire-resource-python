class Web:
    """The HTTP-serving process; buffers one request queue time sample per request."""

    def __init__(self, name):
        self.name = str(name)

    def sample(self, request_queue_time):
        from hirefire_resource.hirefire import HireFire

        HireFire.configuration.buffer.sample_web(request_queue_time)
