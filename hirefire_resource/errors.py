class MissingQueueError(Exception):
    """Raised when a queue macro is called without any queue names.

    Celery macros require at least one queue. RQ macros may omit queues to measure
    every queue present.
    """

    def __init__(self) -> None:
        super().__init__("No queue was specified. Please specify at least one queue.")


class MissingSamplerError(Exception):
    """Raised when ``dyno`` cannot resolve a source without a sampler.

    Bare ``dyno("web")`` is valid: the ``"web"`` name implies http without a sampler.
    """


class DuplicateDynoError(Exception):
    """Raised when a dyno name was already declared for the same source kind.

    Names are compared case-insensitively. At most one http source may exist per
    app process.
    """
