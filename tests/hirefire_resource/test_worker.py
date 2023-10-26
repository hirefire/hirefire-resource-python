import json

from hirefire_resource.worker import Worker


def test_call():
    worker = Worker("worker", lambda: 1.23)
    assert worker.call() == bytes(json.dumps(1.23), "utf-8")
