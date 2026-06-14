class Web:
    """Thin wrapper representing the HTTP-serving process for request queue time
    tracking. The whole HTTP family (RequestQueueTime, RequestsPerMinute) rides
    this one feed; the server derives queue time from the sample values and
    request rate from the sample counts."""

    def __init__(self, name):
        self.name = str(name)

    def sample(self, request_queue_time):
        from hirefire_resource.hirefire import HireFire

        HireFire.configuration.buffer.sample_web(request_queue_time)
