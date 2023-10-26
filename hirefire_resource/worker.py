import json


class Worker:
    def __init__(self, id, proc):
        self.id = id
        self.proc = proc

    def call(self):
        value = self.proc()
        payload = json.dumps(value)
        return bytes(payload, "utf-8")
