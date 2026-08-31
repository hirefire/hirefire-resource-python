from hirefire_resource.strategy import rqt


def test_rqt_accepts_string():
    assert rqt("rqt") is True


def test_rqt_rejects_other_strategies():
    assert rqt("jql") is False
    assert rqt(None) is False
