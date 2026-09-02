import logging

from hirefire_resource.log import format_error, safe_log


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


def test_safe_with_none_logger():
    safe_log(None, "error", "boom")


def test_format_error_strips_url_userinfo():
    error = RuntimeError("redis://user:secret@127.0.0.1:6379/0 failed")
    text = format_error(error)
    assert "secret" not in text
    assert "redis://***@127.0.0.1:6379/0" in text
