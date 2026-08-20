from hirefire_resource.source.job_queue import JobQueue


def test_name_and_sample():
    job_queue = JobQueue("worker", lambda: 1 + 1)
    assert job_queue.name == "worker"
    assert job_queue.sample() == 2


def test_name_normalized_to_string():
    job_queue = JobQueue(123, lambda: 1)
    assert job_queue.name == "123"
