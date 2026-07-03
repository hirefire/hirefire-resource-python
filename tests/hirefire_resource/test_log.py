import logging

from hirefire_resource.log import safe_log


def test_delegates_to_the_logger(caplog):
    logger = logging.getLogger("hirefire_resource.test_log")
    with caplog.at_level(logging.ERROR, logger=logger.name):
        safe_log(logger, "error", "boom")
    assert "boom" in caplog.text


def test_swallows_a_raising_logger():
    class RaisingLogger:
        def error(self, message):
            raise IOError("closed stream")

    safe_log(RaisingLogger(), "error", "boom")


def test_skips_a_logger_that_does_not_respond_to_the_level():
    safe_log(object(), "error", "boom")
