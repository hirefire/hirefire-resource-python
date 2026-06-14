from hirefire_resource.worker import Worker


def test_name_and_sample():
    worker = Worker("worker", lambda: 1 + 1)
    assert worker.name == "worker"
    assert worker.sample() == 2


def test_name_normalized_to_string():
    worker = Worker(123, lambda: 1)
    assert worker.name == "123"
