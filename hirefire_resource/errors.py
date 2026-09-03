class MissingQueueError(Exception):
    def __init__(self) -> None:
        super().__init__("No queue was specified. Please specify at least one queue.")


class MissingSamplerError(Exception):
    pass


class DuplicateDynoError(Exception):
    pass
