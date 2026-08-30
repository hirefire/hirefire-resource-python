import threading
import time

from freezegun import freeze_time

from hirefire_resource.buffer import Buffer
from tests.helpers import at


def test_sample_rqt_accumulates_sum_and_count():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample("web", "rqt", 10)
        buffer.sample("web", "rqt", 20)
        buffer.sample("web", "rqt", 30)

    data = buffer.flush()
    assert data["web"]["rqt"][100] == {"sum": 60.0, "count": 3}


def test_discard_inherited_clears_all_strategies():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample("web", "rqt", 7)
        buffer.sample("worker", "jql", 5)
        buffer.sample("web", "cpu", 12.5)
        buffer.discard_inherited()
        assert buffer.flush() == {}


def test_rqt_caps_count_at_sample_count_limit():
    buffer = Buffer()
    with freeze_time(at(100)):
        series = buffer._series_for("web", "rqt")
        series[100] = {"sum": 0.0, "count": Buffer.SAMPLE_COUNT_LIMIT}
        buffer.sample("web", "rqt", 1)

        data = buffer.flush()
        bucket = data["web"]["rqt"][100]
        assert bucket["count"] == Buffer.SAMPLE_COUNT_LIMIT
        assert abs(bucket["sum"] - 0.0) < 0.0001


def test_sample_ignores_non_finite_and_non_numeric():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample("web", "rqt", True)
        buffer.sample("web", "rqt", float("nan"))
        buffer.sample("web", "rqt", float("inf"))
        buffer.sample("web", "cpu", "nope")
        buffer.sample("web", "rqt", 5)
        buffer.sample("worker", "jql", False)

    data = buffer.flush()
    assert data["web"]["rqt"][100] == {"sum": 5.0, "count": 1}
    assert "cpu" not in data.get("web", {})
    assert "worker" not in data


def test_reinit_after_fork_clears_metrics_and_replaces_mutex():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample("web", "rqt", 7)
        old_mutex = buffer._mutex
        buffer.reinit_after_fork()
        assert buffer._mutex is not old_mutex
        assert buffer.flush() == {}


def test_repopulate_rejects_non_rqt_strategy():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.repopulate("web", "cpu", {100: {"sum": 1.0, "count": 1}})
        buffer.repopulate("worker", "jql", {100: 5})
        assert buffer.flush() == {}


def test_non_rqt_latest_wins_bare_scalar():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample("worker", "jql", 42)
        buffer.sample("worker", "jql", 7)
        buffer.sample("web", "cpu", 10.0)
        buffer.sample("web", "cpu", 37.5)

    data = buffer.flush()
    assert data["worker"]["jql"][100] == 7
    assert data["web"]["cpu"][100] == 37.5


def test_sample_rqt_groups_by_timestamp():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample("web", "rqt", 12)
    with freeze_time(at(101)):
        buffer.sample("web", "rqt", 8)

    data = buffer.flush()
    assert data["web"]["rqt"] == {
        100: {"sum": 12.0, "count": 1},
        101: {"sum": 8.0, "count": 1},
    }


def test_sample_job_strategies():
    buffer = Buffer()
    buffer.sample("worker", "jql", 42)
    buffer.sample("mailer", "jqs", 18)

    data = buffer.flush()
    assert list(data["worker"]["jql"].values())[0] == 42
    assert list(data["mailer"]["jqs"].values())[0] == 18


def test_flush_returns_and_resets():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample("web", "rqt", 5)
        buffer.sample("worker", "jql", 10)

    data = buffer.flush()
    assert data["web"]["rqt"] == {100: {"sum": 5.0, "count": 1}}
    assert data["worker"]["jql"] == {100: 10}

    data = buffer.flush()
    assert data == {}


def test_multi_strategy_under_one_name():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample("web", "rqt", 12)
        buffer.sample("web", "cpu", 37.5)

    data = buffer.flush()
    assert data["web"]["rqt"] == {100: {"sum": 12.0, "count": 1}}
    assert data["web"]["cpu"] == {100: 37.5}


def test_sample_rqt_bounded_when_dispatch_is_starved():
    buffer = Buffer()
    with freeze_time(at(1000)) as frozen:
        for second in range(1000, 1071):
            frozen.move_to(at(second))
            buffer.sample("web", "rqt", 1)

    data = buffer.flush()
    assert len(data["web"]["rqt"]) <= 66
    assert min(data["web"]["rqt"].keys()) == 1006
    assert max(data["web"]["rqt"].keys()) == 1070


def test_sample_cpu_bounded_when_dispatch_is_starved():
    buffer = Buffer()
    with freeze_time(at(1000)) as frozen:
        for second in range(1000, 1071):
            frozen.move_to(at(second))
            buffer.sample("clock", "cpu", 50.0)

    data = buffer.flush()
    assert len(data["clock"]["cpu"]) <= 66
    assert max(data["clock"]["cpu"].keys()) == 1070


def test_repopulate_rqt_within_ttl():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.repopulate(
            "web",
            "rqt",
            {90: {"sum": 5.0, "count": 1}, 30: {"sum": 10.0, "count": 1}},
        )

    data = buffer.flush()
    assert data["web"]["rqt"] == {90: {"sum": 5.0, "count": 1}}
    assert 30 not in data["web"]["rqt"]


def test_vector_c_repopulate_merge_sum_and_count():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.repopulate("web", "rqt", {100: {"sum": 10.0, "count": 1}})
        buffer.sample("web", "rqt", 15)
        buffer.sample("web", "rqt", 15)

    assert buffer.flush()["web"]["rqt"][100] == {"sum": 40.0, "count": 3}


def test_repopulate_clamps_to_sample_count_limit():
    buffer = Buffer()
    limit = Buffer.SAMPLE_COUNT_LIMIT
    with freeze_time(at(100)):
        buffer.repopulate("web", "rqt", {100: {"sum": float(limit), "count": limit}})
        buffer.repopulate("web", "rqt", {100: {"sum": 100.0, "count": 100}})
        bucket = buffer.flush()["web"]["rqt"][100]
        assert bucket["count"] == limit
        assert abs(bucket["sum"] / bucket["count"] - 1.0) < 0.001


def test_repopulate_skips_non_hash_and_non_positive_count():
    buffer = Buffer()
    with freeze_time(at(200)):
        buffer.repopulate(
            "web",
            "rqt",
            {
                190: 12,
                191: {"sum": 5.0, "count": 0},
                192: {"sum": 7.0, "count": -1},
                193: {"sum": 9.0, "count": 1},
            },
        )

    assert buffer.flush()["web"]["rqt"] == {193: {"sum": 9.0, "count": 1}}


def test_concurrent_sample_flush_and_repopulate():
    buffer = Buffer()
    errors = []
    lock = threading.Lock()

    def worker(i):
        try:
            for j in range(80):
                buffer.sample("web", "rqt", j)
                if j % 2 == 0:
                    buffer.sample("worker", "jql", j)
                if j % 10 == 0:
                    buffer.repopulate(
                        "web", "rqt", {int(time.time()): {"sum": float(i), "count": 1}}
                    )
                if j % 17 == 0:
                    buffer.flush()
        except Exception as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    data = buffer.flush()
    assert isinstance(data, dict)


def test_repopulate_ignores_array_buckets():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.repopulate("web", "rqt", {100: [12, 8]})

    assert buffer.flush() == {}


def test_rqt_caps_count_and_freezes_sum_once_at_limit():
    buffer = Buffer()
    limit = Buffer.SAMPLE_COUNT_LIMIT
    with freeze_time(at(100)):
        series = buffer._series_for("web", "rqt")
        series[100] = {"sum": 2.0 * (limit - 1), "count": limit - 1}
        buffer.sample("web", "rqt", 2.0)
        buffer.sample("web", "rqt", 2.0)
        buffer.sample("web", "rqt", 2.0)
        bucket = buffer.flush()["web"]["rqt"][100]
        assert bucket["count"] == limit
        assert bucket["sum"] == 2.0 * limit
