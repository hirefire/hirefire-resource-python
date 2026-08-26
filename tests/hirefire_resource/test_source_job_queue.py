from hirefire_resource.source.job_queue import JobQueue


def test_name():
    job_queue = JobQueue("worker", lambda: 1 + 1)
    assert job_queue.name == "worker"


def test_sample_returns_the_sampler_result():
    job_queue = JobQueue("worker", lambda: 1)
    assert job_queue.sample() == 1


def test_name_normalized_to_string():
    job_queue = JobQueue(123, lambda: 1)
    assert job_queue.name == "123"
