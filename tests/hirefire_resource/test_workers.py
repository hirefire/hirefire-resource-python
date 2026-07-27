import logging

from hirefire_resource import HireFire
from hirefire_resource.worker import Worker
from hirefire_resource.workers import Workers


def buffer():
    return HireFire.configuration.buffer


def _strategy_value(data, name, strategy):
    return list(data[name][strategy].values())[0]


def _sample_all(strategy="jql"):
    workers = HireFire.configuration.job_queues
    for worker in workers:
        workers.sample_job_queue(worker, strategy)


def test_sample_job_queue_jql():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
        config.dyno("mailer", lambda: 18)

    _sample_all("jql")

    data = buffer().flush()
    assert _strategy_value(data, "worker", "jql") == 42
    assert _strategy_value(data, "mailer", "jql") == 18


def test_sample_job_queue_jqs():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 7)

    _sample_all("jqs")
    assert _strategy_value(buffer().flush(), "worker", "jqs") == 7


def test_find_by_name_case_insensitive():
    with HireFire.configure() as config:
        config.dyno("Worker", lambda: 1)

    found = HireFire.configuration.job_queues.find_by_name("worker")
    assert found is not None
    assert found.name == "Worker"


def test_raising_sampler_is_isolated_and_logged(caplog):
    caplog.set_level(logging.ERROR)

    def boom():
        raise RuntimeError("Redis down")

    with HireFire.configure() as config:
        config.dyno("worker", boom)
        config.dyno("mailer", lambda: 18)

    _sample_all()

    data = buffer().flush()
    assert "worker" not in data or "jql" not in data.get("worker", {})
    assert _strategy_value(data, "mailer", "jql") == 18
    assert "Redis down" in caplog.text


def test_invalid_sample_values_are_dropped_and_logged(caplog):
    caplog.set_level(logging.ERROR)
    values = iter(["10", None, -1, float("inf"), float("nan"), 7])
    with HireFire.configure() as config:
        config.dyno("worker", lambda: next(values))

    for _ in range(5):
        _sample_all()
    assert buffer().flush() == {}
    assert "expected a non-negative number" in caplog.text

    _sample_all()
    assert _strategy_value(buffer().flush(), "worker", "jql") == 7


def test_a_boolean_sample_is_dropped(caplog):
    caplog.set_level(logging.ERROR)
    with HireFire.configure() as config:
        config.dyno("worker", lambda: True)

    _sample_all()

    assert buffer().flush() == {}
    assert "expected a non-negative number" in caplog.text


def test_unknown_strategy_dropped(caplog):
    caplog.set_level(logging.ERROR)
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 1)

    worker = list(HireFire.configuration.job_queues)[0]
    HireFire.configuration.job_queues.sample_job_queue(worker, "nope")
    assert buffer().flush() == {}
    assert "Unknown job-queue strategy" in caplog.text


def test_iteration_and_names():
    workers = Workers()
    workers.append(Worker("worker", lambda: 1))
    workers.append(Worker("mailer", lambda: 2))
    assert [worker.name for worker in workers] == ["worker", "mailer"]


def test_any_and_len():
    workers = Workers()
    assert not workers.any()
    assert len(workers) == 0

    workers.append(Worker("worker", lambda: 1))
    assert workers.any()
    assert len(workers) == 1


def test_zero_sample_is_accepted():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 0)

    _sample_all()
    assert _strategy_value(buffer().flush(), "worker", "jql") == 0


def test_a_raising_logger_does_not_escape_sampling():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: (_ for _ in ()).throw(RuntimeError("down")))

    class RaisingLogger:
        def error(self, message):
            raise IOError("closed stream")

    HireFire.configuration.logger = RaisingLogger()
    _sample_all()
