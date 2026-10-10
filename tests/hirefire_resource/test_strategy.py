from hirefire_resource import strategy
from hirefire_resource.strategy import job_queue, rqt


def test_names_every_series():
    assert (strategy.RQT, strategy.JQL, strategy.JQS, strategy.CPU, strategy.WRK) == (
        "rqt",
        "jql",
        "jqs",
        "cpu",
        "wrk",
    )


def test_rqt_accepts_string():
    assert rqt("rqt") is True


def test_rqt_rejects_other_strategies():
    assert rqt("jql") is False
    assert rqt(None) is False


def test_job_queue_accepts_latency_and_size_only():
    assert job_queue("jql") is True
    assert job_queue("jqs") is True
    assert job_queue("wrk") is False
    assert job_queue("rqt") is False
    assert job_queue(None) is False
