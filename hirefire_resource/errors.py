class MissingQueueError(Exception):
    """Raised when a queue macro is called without any queue names.

    Celery macros require at least one queue. RQ macros may omit queues to measure
    every queue present.
    """

    def __init__(self) -> None:
        super().__init__("No queue was specified. Please specify at least one queue.")


class MissingSamplerError(Exception):
    """Raised when ``dyno`` is given without a sampler.

    Bare ``dyno("web")`` is a deprecated no-op (request queue time is sampled from
    HTTP traffic). Any other name needs a job-queue sampler.
    """


class DuplicateDynoError(Exception):
    """Raised when a dyno name was already declared for the same source kind.

    Names are compared case-insensitively. Job-queue samplers share a name
    namespace per process.
    """
