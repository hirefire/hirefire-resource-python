import logging
import time

from hirefire_resource.probe import Probe


def test_start_returns_a_probe():
    probe = Probe.start()
    assert isinstance(probe, Probe)


def test_finish_empty_ops():
    probe = Probe.start()
    payload = probe.finish()

    assert isinstance(payload["wave_ms"], (int, float))
    assert payload["wave_ms"] >= 0
    assert payload["ops"] == []


def test_record_builds_op_shape():
    probe = Probe.start()
    entry = {
        "adapter": "celery",
        "strategy": "jql",
        "queues": ["default", "mailers"],
        "options": {"schema": "public"},
    }
    probe.record(entry, 12.3456)
    payload = probe.finish()

    assert len(payload["ops"]) == 1
    op = payload["ops"][0]
    assert op["adapter"] == "celery"
    assert op["strategy"] == "jql"
    assert op["queues"] == ["default", "mailers"]
    assert op["options"] == {"schema": "public"}
    assert op["ms"] == 12.346


def test_record_normalizes_missing_and_wrong_type_fields():
    probe = Probe.start()
    probe.record(
        {
            "adapter": None,
            "strategy": "jqs",
            "queues": "default",
            "options": ["x"],
        },
        1.0,
    )
    op = probe.finish()["ops"][0]

    assert op["adapter"] is None
    assert op["strategy"] == "jqs"
    assert op["queues"] == []
    assert op["options"] == {}
    assert op["ms"] == 1.0


def test_record_none_strategy_is_empty_string():
    probe = Probe.start()
    probe.record({"adapter": "a", "strategy": None}, 0.5)
    assert probe.finish()["ops"][0]["strategy"] == ""


def test_record_non_dict_entry_coerces():
    probe = Probe.start()
    probe.record(None, 2.0)
    probe.record("bad", 3.0)
    ops = probe.finish()["ops"]

    assert len(ops) == 2
    for op in ops:
        assert op["adapter"] is None
        assert op["strategy"] == ""
        assert op["queues"] == []
        assert op["options"] == {}
    assert ops[0]["ms"] == 2.0
    assert ops[1]["ms"] == 3.0


def test_measure_times_callable_and_records():
    probe = Probe.start()
    called = {"v": False}

    def work():
        called["v"] = True
        time.sleep(0.01)
        return "ok"

    result = probe.measure({"adapter": "a", "strategy": "jql", "queues": ["q"]}, work)

    assert called["v"] is True
    assert result == "ok"
    op = probe.finish()["ops"][0]
    assert op["adapter"] == "a"
    assert op["strategy"] == "jql"
    assert op["queues"] == ["q"]
    assert isinstance(op["ms"], (int, float))
    assert op["ms"] >= 5


def test_measure_does_not_record_when_callable_raises():
    probe = Probe.start()

    def boom():
        raise RuntimeError("boom")

    try:
        probe.measure({"strategy": "jql"}, boom)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")

    assert probe.finish()["ops"] == []


def test_measure_keeps_prior_ops_when_later_raises():
    probe = Probe.start()
    probe.measure({"strategy": "jql"}, lambda: None)

    def boom():
        raise RuntimeError("boom")

    try:
        probe.measure({"strategy": "jqs"}, boom)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")

    payload = probe.finish()
    assert len(payload["ops"]) == 1
    assert payload["ops"][0]["strategy"] == "jql"
    assert isinstance(payload["wave_ms"], (int, float))


def test_finish_wave_ms_covers_all_ops():
    probe = Probe.start()
    probe.measure({"strategy": "jql"}, lambda: time.sleep(0.01))
    probe.measure({"strategy": "jqs"}, lambda: time.sleep(0.01))
    payload = probe.finish()
    ops_ms = sum(op["ms"] for op in payload["ops"])

    assert len(payload["ops"]) == 2
    for op in payload["ops"]:
        assert payload["wave_ms"] >= op["ms"]
    assert payload["wave_ms"] + 1.0 >= ops_ms
    assert payload["wave_ms"] >= 10


def test_finish_is_stable_when_called_twice():
    probe = Probe.start()
    probe.record({"strategy": "jql"}, 3.0)
    first = probe.finish()
    second = probe.finish()

    assert first is second
    assert first["wave_ms"] == second["wave_ms"]


def test_finish_ops_isolated_from_later_record():
    probe = Probe.start()
    probe.record({"strategy": "jql"}, 1.0)
    first = probe.finish()
    first_wave_ms = first["wave_ms"]
    first_ops = first["ops"]

    time.sleep(0.01)
    probe.record({"strategy": "jqs"}, 2.0)
    second = probe.finish()

    assert len(first_ops) == 1
    assert first_ops[0]["strategy"] == "jql"
    assert first["wave_ms"] == first_wave_ms
    assert len(second["ops"]) == 2
    assert first is not second
    assert second["wave_ms"] >= first_wave_ms


def test_log_writes_wave_and_per_op_lines(caplog):
    probe = Probe.start()
    probe.record(
        {"adapter": "celery", "strategy": "jql", "queues": ["default"]},
        4.5,
    )
    logger = logging.getLogger("hirefire_probe_test")
    with caplog.at_level(logging.INFO, logger=logger.name):
        probe.log_to(logger)

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "sample_job_queues wave_ms=" in text
    assert "ops=1" in text
    assert "sample adapter='celery' strategy=jql" in text
    assert "queues=default" in text
    assert "ms=4.5" in text
