class MissingQueueError(Exception):
    def __init__(self):
        super().__init__("No queue was specified. Please specify at least one queue.")
