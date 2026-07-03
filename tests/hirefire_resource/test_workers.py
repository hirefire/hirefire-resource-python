import logging

from hirefire_resource import HireFire
from hirefire_resource.worker import Worker
from hirefire_resource.workers import Workers


def buffer():
    return HireFire.configuration.buffer


def test_sample():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: 42)
        config.dyno("mailer", lambda: 18)

    HireFire.configuration.workers.sample()

    data = buffer().flush()
    assert data["workers"] == [
        {"name": "worker", "sample": 42},
        {"name": "mailer", "sample": 18},
    ]


def test_latest_sample_wins_across_multiple_samples():
    values = iter([5, 9])
    with HireFire.configure() as config:
        config.dyno("worker", lambda: next(values))

    HireFire.configuration.workers.sample()
    HireFire.configuration.workers.sample()

    data = buffer().flush()
    assert data["workers"] == [{"name": "worker", "sample": 9}]


def test_raising_sampler_is_isolated_and_logged(caplog):
    caplog.set_level(logging.ERROR)

    def boom():
        raise RuntimeError("Redis down")

    with HireFire.configure() as config:
        config.dyno("worker", boom)
        config.dyno("mailer", lambda: 18)

    HireFire.configuration.workers.sample()

    data = buffer().flush()
    assert data["workers"] == [{"name": "mailer", "sample": 18}]
    assert "Redis down" in caplog.text


def test_invalid_sample_values_are_dropped_and_logged(caplog):
    caplog.set_level(logging.ERROR)
    values = iter(["10", None, -1, float("inf"), float("nan"), 7])
    with HireFire.configure() as config:
        config.dyno("worker", lambda: next(values))

    for _ in range(5):
        HireFire.configuration.workers.sample()
    assert buffer().flush()["workers"] == []
    assert "expected a non-negative number" in caplog.text

    HireFire.configuration.workers.sample()
    assert buffer().flush()["workers"] == [{"name": "worker", "sample": 7}]


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

    HireFire.configuration.workers.sample()

    assert buffer().flush()["workers"] == [{"name": "worker", "sample": 0}]


def test_a_raising_logger_does_not_escape_sampling():
    with HireFire.configure() as config:
        config.dyno("worker", lambda: (_ for _ in ()).throw(RuntimeError("down")))

    class RaisingLogger:
        def error(self, message):
            raise IOError("closed stream")

    HireFire.configuration.logger = RaisingLogger()

    HireFire.configuration.workers.sample()
