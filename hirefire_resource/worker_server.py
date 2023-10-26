import json


class WorkerServer:
    def __init__(self, token, measure):
        self.token = token
        self._measure = measure

    def serve(self):
        value = self._measure()
        return bytes(json.dumps(value), "utf-8")
