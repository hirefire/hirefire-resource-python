class MissingQueueError(Exception):
    """Raised when a queue macro is called without any queue names.

    Celery macros require at least one queue. RQ macros may omit queues to measure
    every queue present.
    """

    def __init__(self) -> None:
        super().__init__("No queue was specified. Please specify at least one queue.")
