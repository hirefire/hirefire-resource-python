import asyncio

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


def test_sample_awaits_an_async_sampler():
    async def sampler():
        return 7

    assert JobQueue("worker", sampler).sample() == 7


def test_sample_awaits_a_coroutine_returned_by_a_sync_sampler():
    async def size():
        return 3

    assert JobQueue("worker", lambda: size()).sample() == 3


def test_sample_awaits_a_sampler_that_yields_to_the_event_loop():
    async def sampler():
        return await asyncio.to_thread(lambda: 5)

    assert JobQueue("worker", sampler).sample() == 5


def test_sample_awaits_on_every_call():
    values = iter([1, 2])

    async def sampler():
        return next(values)

    job_queue = JobQueue("worker", sampler)
    assert [job_queue.sample(), job_queue.sample()] == [1, 2]
