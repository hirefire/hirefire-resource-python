from hirefire_resource.buffer import Buffer
from hirefire_resource.strategy import RQT


class HTTP:
    def __init__(self, name: str, buffer: Buffer) -> None:
        self.name = str(name)
        self._buffer = buffer

    def sample(self, request_queue_time: int) -> None:
        self._buffer.sample(self.name, RQT, request_queue_time)
