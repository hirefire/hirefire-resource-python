class MissingQueueError(Exception):
    def __init__(self) -> None:
        super().__init__("No queue was specified. Please specify at least one queue.")


class MissingSamplerError(Exception):
    pass


class DuplicateDynoError(Exception):
    pass


class JobQueueLatencyUnsupportedError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"{name} currently does not support job queue latency measurements."
        )
        self.name = name
