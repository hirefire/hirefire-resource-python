from freezegun import freeze_time

from hirefire_resource.buffer import Buffer
from tests.helpers import at


def test_sample_web():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample_web(12)
        buffer.sample_web(8)

    data = buffer.flush()
    assert data["web"] == {100: [12, 8]}


def test_sample_web_groups_by_timestamp():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample_web(12)
    with freeze_time(at(101)):
        buffer.sample_web(8)

    data = buffer.flush()
    assert data["web"] == {100: [12], 101: [8]}


def test_sample_worker():
    buffer = Buffer()
    buffer.sample_worker("worker", 42)
    buffer.sample_worker("mailer", 18)

    data = buffer.flush()
    assert data["workers"] == [
        {"name": "worker", "sample": 42},
        {"name": "mailer", "sample": 18},
    ]


def test_flush_returns_all_and_resets():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample_web(5)
    buffer.sample_worker("worker", 10)

    data = buffer.flush()
    assert data["web"] == {100: [5]}
    assert data["workers"] == [{"name": "worker", "sample": 10}]

    data = buffer.flush()
    assert data["web"] == {}
    assert data["workers"] == []


def test_sample_worker_latest_wins_per_name():
    buffer = Buffer()
    buffer.sample_worker("worker", 42)
    buffer.sample_worker("mailer", 18)
    buffer.sample_worker("worker", 7)

    data = buffer.flush()
    assert data["workers"] == [
        {"name": "worker", "sample": 7},
        {"name": "mailer", "sample": 18},
    ]


def test_sample_web_bounded_when_dispatch_is_starved():
    buffer = Buffer()
    for second in range(1000, 1071):
        with freeze_time(at(second)):
            buffer.sample_web(1)

    data = buffer.flush()
    assert len(data["web"]) <= 66
    assert min(data["web"].keys()) == 1006  # seconds beyond the TTL pruned
    assert max(data["web"].keys()) == 1070


def test_sample_cpu_bounded_when_dispatch_is_starved():
    buffer = Buffer()
    for second in range(1000, 1071):
        with freeze_time(at(second)):
            buffer.sample_cpu("clock", 50.0)

    data = buffer.flush()
    assert len(data["cpu"]["clock"]) <= 66
    assert max(data["cpu"]["clock"].keys()) == 1070


def test_repopulate_web_within_ttl():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.repopulate_web({90: [5], 30: [10]})

    data = buffer.flush()
    assert data["web"] == {90: [5]}
    assert 30 not in data["web"]


def test_repopulate_web_merges_with_existing():
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.sample_web(1)
        buffer.repopulate_web({100: [2, 3]})

    data = buffer.flush()
    assert data["web"][100] == [1, 2, 3]


def test_flush_returns_and_resets_cpu():
    buffer = Buffer()
    with freeze_time(at(1000)):
        buffer.sample_cpu("clock", 50.0)

    data = buffer.flush()
    assert data["cpu"] == {"clock": {1000: [50.0]}}
    assert buffer.flush()["cpu"] == {}  # second flush is reset


def test_sample_cpu_groups_values_within_a_second():
    buffer = Buffer()
    with freeze_time(at(1000)):
        buffer.sample_cpu("clock", 40.0)
        buffer.sample_cpu("clock", 60.0)

    assert buffer.flush()["cpu"] == {"clock": {1000: [40.0, 60.0]}}


def test_repopulate_web_keeps_the_second_exactly_at_the_ttl_boundary():
    # 40 == now - ttl: the boundary second is inside the window (drop is `<`).
    buffer = Buffer()
    with freeze_time(at(100)):
        buffer.repopulate_web({40: [5]})

    assert buffer.flush()["web"] == {40: [5]}
