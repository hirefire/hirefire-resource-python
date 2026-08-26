import logging

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
from hirefire_resource.source.job_queue import JobQueue
from hirefire_resource.source.job_queues import JobQueues


def buffer():
    return HireFire.configuration.buffer


def _strategy_value(data, name, strategy):
    return list(data[name][strategy].values())[0]


def test_sample_job_queue():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
        config.dyno("mailer", lambda: 18)

    job_queues = HireFire.configuration.job_queues
    job_queues.sample_job_queue(job_queues.find_by_name("worker"), "jql")
    job_queues.sample_job_queue(job_queues.find_by_name("mailer"), "jqs")

    data = buffer().flush()
    assert _strategy_value(data, "worker", "jql") == 42
    assert _strategy_value(data, "mailer", "jqs") == 18


def test_sample_job_queue_rejects_unknown_strategy(caplog):
    caplog.set_level(logging.ERROR)
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)

    job_queue = HireFire.configuration.job_queues.find_by_name("worker")
    HireFire.configuration.job_queues.sample_job_queue(job_queue, "rpm")
    assert buffer().flush() == {}
    assert "Unknown job-queue strategy" in caplog.text


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


def test_latest_sample_wins_across_multiple_samples():
    values = iter([5, 9])
    with HireFire.configure() as config:
        config.dyno("worker", lambda: next(values))

    job_queue = HireFire.configuration.job_queues.find_by_name("worker")
    HireFire.configuration.job_queues.sample_job_queue(job_queue, "jql")
    HireFire.configuration.job_queues.sample_job_queue(job_queue, "jql")

    assert _strategy_value(buffer().flush(), "worker", "jql") == 9


def test_raising_sampler_is_isolated_and_logged(caplog):
    caplog.set_level(logging.ERROR)

    def boom():
        raise RuntimeError("Redis down")

    with HireFire.configure() as config:
        config.dyno("worker", boom)
        config.dyno("mailer", lambda: 18)

    job_queues = HireFire.configuration.job_queues
    job_queues.sample_job_queue(job_queues.find_by_name("worker"), "jql")
    job_queues.sample_job_queue(job_queues.find_by_name("mailer"), "jql")

    data = buffer().flush()
    assert "worker" not in data or "jql" not in data.get("worker", {})
    assert _strategy_value(data, "mailer", "jql") == 18
    assert "Redis down" in caplog.text


def test_invalid_sample_values_are_dropped_and_logged(caplog):
    caplog.set_level(logging.ERROR)
    values = iter(["10", None, -1, float("inf"), float("nan"), True, False, 7])
    with HireFire.configure() as config:
        config.dyno("worker", lambda: next(values))

    job_queue = HireFire.configuration.job_queues.find_by_name("worker")
    for _ in range(7):
        HireFire.configuration.job_queues.sample_job_queue(job_queue, "jql")
    assert buffer().flush() == {}
    assert "expected a non-negative number" in caplog.text

    HireFire.configuration.job_queues.sample_job_queue(job_queue, "jql")
    assert _strategy_value(buffer().flush(), "worker", "jql") == 7


def test_a_raising_logger_does_not_escape_sampling():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: (_ for _ in ()).throw(RuntimeError("down")))

    class RaisingLogger:
        def error(self, message):
            raise IOError("closed stream")

    HireFire.configuration.logger = RaisingLogger()
    job_queue = HireFire.configuration.job_queues.find_by_name("worker")
    HireFire.configuration.job_queues.sample_job_queue(job_queue, "jql")


def test_samples_write_to_the_owning_configuration_not_the_global(monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "old-token")
    old = Configuration()
    old.dyno("worker", lambda: 7)
    job_queue = old.job_queues.find_by_name("worker")

    HireFire.reset()
    monkeypatch.setenv("HIREFIRE_TOKEN", "new-token")
    HireFire.configuration.dyno("web")

    old.job_queues.sample_job_queue(job_queue, "jql")

    assert "worker" not in HireFire.configuration.buffer.flush()
    assert _strategy_value(old.buffer.flush(), "worker", "jql") == 7


def test_sample_job_queue_reports_under_explicit_name():
    with HireFire.configure() as config:
        config.dyno("Worker", lambda: 4)

    job_queue = HireFire.configuration.job_queues.find_by_name("worker")
    HireFire.configuration.job_queues.sample_job_queue(job_queue, "jqs", name="worker")

    data = buffer().flush()
    assert _strategy_value(data, "worker", "jqs") == 4
    assert "Worker" not in data


def test_live_gate_drops_a_sample_that_returns_after_stop():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 9)

    job_queue = HireFire.configuration.job_queues.find_by_name("worker")
    HireFire.configuration.job_queues.sample_job_queue(
        job_queue, "jql", live=lambda: False
    )

    assert buffer().flush() == {}


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


def test_zero_sample_is_accepted():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 0)

    job_queue = HireFire.configuration.job_queues.find_by_name("worker")
    HireFire.configuration.job_queues.sample_job_queue(job_queue, "jql")
    assert _strategy_value(buffer().flush(), "worker", "jql") == 0
