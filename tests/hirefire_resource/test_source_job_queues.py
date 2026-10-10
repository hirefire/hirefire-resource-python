from hirefire_resource import HireFire
from hirefire_resource.source.job_queue import JobQueue
from hirefire_resource.source.job_queues import JobQueues


def test_find_by_name_returns_none_for_missing():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 1)

    assert HireFire.configuration.job_queues.find_by_name("missing") is None


def test_find_by_name_is_case_insensitive_and_preserves_canonical_name():
    with HireFire.configure() as config:
        config.dyno("Worker", lambda: 1)

    found = HireFire.configuration.job_queues.find_by_name("worker")
    assert found is not None
    assert found.name == "Worker"
    assert found is HireFire.configuration.job_queues.find_by_name("WORKER")


def test_enumerable():
    job_queues = JobQueues()
    job_queues.append(JobQueue("worker", lambda: 1))
    job_queues.append(JobQueue("mailer", lambda: 2))
    assert [job_queue.name for job_queue in job_queues] == ["worker", "mailer"]


def test_any_and_len():
    job_queues = JobQueues()
    assert not job_queues.any()
    assert len(job_queues) == 0

    job_queues.append(JobQueue("worker", lambda: 1))
    assert job_queues.any()
    assert len(job_queues) == 1
