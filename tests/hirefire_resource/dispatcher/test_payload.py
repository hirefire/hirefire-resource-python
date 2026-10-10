import pytest
from freezegun import freeze_time

from hirefire_resource.dispatcher import payload
from tests.helpers import at


def left_out(name, strategy):
    pytest.fail(f"{strategy} of {name} was left out")


def build(data, liveness=None, since=None, trace=None, omitted=left_out):
    return payload.build(
        data, liveness=liveness, since=since, trace=trace, omitted=omitted
    )


def rqt(total, count):
    return {"sum": total, "count": count}


def test_an_empty_buffer_without_liveness_gives_no_entry_and_no_watermark():
    assert build({}) == ([], None)


def test_job_and_cpu_values_are_sent_as_bare_numbers_under_their_second():
    data = {
        "worker": {"jqs": {1000: 12, 1001: 0}, "wrk": {1000: 3}},
        "clock": {"cpu": {1000: 12.5}},
    }

    entries, watermark = build(data)

    assert entries == [
        {
            "name": "worker",
            "metrics": {"jqs": {"1000": 12, "1001": 0}, "wrk": {"1000": 3}},
        },
        {"name": "clock", "metrics": {"cpu": {"1000": 12.5}}},
    ]
    assert watermark is None


def test_request_queue_time_is_sent_as_its_mean_and_its_count():
    entries, _ = build({"web": {"rqt": {1000: rqt(30.0, 4), 1001: rqt(0.0, 0)}}})

    assert entries == [
        {"name": "web", "metrics": {"rqt": {"1000": [7.5, 4], "1001": []}}}
    ]


def test_liveness_claims_only_the_current_second_on_the_first_build():
    with freeze_time(at(1000)):
        entries, watermark = build({}, liveness="web")

    assert entries == [{"name": "web", "metrics": {"rqt": {"1000": []}}}]
    assert watermark == 1000


def test_liveness_claims_every_second_after_the_last_one_sent_and_keeps_the_samples():
    with freeze_time(at(1003)):
        entries, watermark = build(
            {"web": {"rqt": {1002: rqt(5.0, 1)}}}, liveness="web", since=1000
        )

    assert entries[0]["metrics"]["rqt"] == {"1001": [], "1002": [5.0, 1], "1003": []}
    assert watermark == 1003


def test_liveness_reaches_back_no_further_than_the_backfill_limit():
    limit = payload.RQT_BACKFILL_LIMIT
    assert limit == 60

    with freeze_time(at(2000)):
        entries, watermark = build({}, liveness="web", since=1000)

    seconds = [int(second) for second in entries[0]["metrics"]["rqt"]]
    assert seconds == list(range(2000 - limit, 2001))
    assert watermark == 2000


def test_a_last_second_in_the_future_claims_the_current_second():
    with freeze_time(at(1000)):
        entries, watermark = build({}, liveness="web", since=1500)

    assert list(entries[0]["metrics"]["rqt"]) == ["1000"]
    assert watermark == 1000


def test_the_entry_with_liveness_comes_first_and_carries_its_other_series():
    data = {
        "worker": {"jqs": {1000: 2}},
        "web": {"rqt": {1000: rqt(8.0, 2)}, "cpu": {1000: 40.0}},
    }

    with freeze_time(at(1000)):
        entries, _ = build(data, liveness="web")

    assert [entry["name"] for entry in entries] == ["web", "worker"]
    assert entries[0]["metrics"] == {
        "rqt": {"1000": [4.0, 2]},
        "cpu": {"1000": 40.0},
    }


def test_without_liveness_buffered_request_queue_time_is_sent_as_it_is():
    with freeze_time(at(1005)):
        entries, watermark = build({"web": {"rqt": {1000: rqt(8.0, 2)}}})

    assert entries[0]["metrics"]["rqt"] == {"1000": [4.0, 2]}
    assert watermark is None


def test_the_buffered_buckets_are_not_changed():
    data = {"web": {"rqt": {1002: rqt(5.0, 1)}}}

    with freeze_time(at(1003)):
        build(data, liveness="web", since=1000)

    assert data == {"web": {"rqt": {1002: {"sum": 5.0, "count": 1}}}}


def test_a_value_at_the_limit_is_sent_and_a_value_over_it_or_under_zero_is_left_out_and_reported():
    limit = payload.METRIC_VALUE_LIMIT
    assert limit == 1e15
    omitted = []
    data = {
        "worker": {"jqs": {1000: limit, 1001: limit + 1e3, 1002: -1, 1003: 0}},
        "web": {
            "rqt": {
                1000: rqt(limit * 2, 1),
                1001: rqt(limit, 1),
                1002: rqt(-5.0, 1),
            }
        },
    }

    entries, _ = build(
        data, omitted=lambda name, strategy: omitted.append((name, strategy))
    )

    assert entries[0]["metrics"]["jqs"] == {"1000": limit, "1003": 0}
    assert entries[1]["metrics"]["rqt"] == {"1001": [limit, 1]}
    assert omitted == [
        ("worker", "jqs"),
        ("worker", "jqs"),
        ("web", "rqt"),
        ("web", "rqt"),
    ]


def test_a_mean_that_is_not_a_number_is_left_out_and_reported():
    omitted = []
    data = {
        "web": {
            "rqt": {1000: rqt(float("inf"), 1), 1001: rqt(float("nan"), 1)},
            "cpu": {1000: float("nan"), 1001: 1.5},
        }
    }

    entries, _ = build(
        data, omitted=lambda name, strategy: omitted.append((name, strategy))
    )

    assert entries == [{"name": "web", "metrics": {"cpu": {"1001": 1.5}}}]
    assert omitted == [("web", "rqt"), ("web", "rqt"), ("web", "cpu")]


def test_a_series_or_an_entry_with_nothing_left_to_send_is_dropped():
    data = {
        "worker": {"jqs": {1000: -1}, "jql": {}},
        "mailer": {"jqs": {1000: 4}},
    }

    entries, _ = build(data, omitted=lambda name, strategy: None)

    assert entries == [{"name": "mailer", "metrics": {"jqs": {"1000": 4}}}]


def test_the_trace_goes_on_the_first_entry_and_nowhere_when_there_is_none():
    trace = {"wave_ms": 1.5, "ops": []}

    entries, _ = build(
        {"worker": {"jqs": {1000: 1}}, "mailer": {"jqs": {1000: 2}}}, trace=trace
    )

    assert entries[0]["sample_trace"] == trace
    assert "sample_trace" not in entries[1]
    assert build({}, trace=trace) == ([], None)


def test_traced_tells_whether_the_first_entry_carries_a_trace():
    plain, _ = build({"worker": {"jqs": {1000: 1}}})
    traced, _ = build({"worker": {"jqs": {1000: 1}}}, trace={"ops": []})

    assert not payload.traced(plain)
    assert payload.traced(traced)


def test_without_trace_returns_the_same_entries_less_the_trace_and_leaves_the_original():
    traced, _ = build(
        {"worker": {"jqs": {1000: 1}}, "mailer": {"jqs": {1000: 2}}},
        trace={"ops": []},
    )

    stripped = payload.without_trace(traced)

    assert stripped == [
        {"name": "worker", "metrics": {"jqs": {"1000": 1}}},
        {"name": "mailer", "metrics": {"jqs": {"1000": 2}}},
    ]
    assert payload.traced(traced)
