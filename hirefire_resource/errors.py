class MissingQueueError(Exception):
    """Raised when a queue macro is called without any queue names and the backend requires them
    (e.g. Celery).
    """

    def __init__(self) -> None:
        super().__init__("No queue was specified. Please specify at least one queue.")


class MissingSamplerError(Exception):
    """Raised when :meth:`Configuration.dyno` cannot resolve a source because a name
    was given without a sampler (except bare ``"web"``, which is a no-op for
    backwards compatibility).
    """


class DuplicateDynoError(Exception):
    """Raised when a dyno name was already declared for the same source kind
    (names are compared case-insensitively).
    """
