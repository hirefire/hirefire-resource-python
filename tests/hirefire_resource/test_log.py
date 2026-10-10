import io
import logging

import pytest

from hirefire_resource.log import format_error, safe_log
from tests.helpers import STARTING, boot_in_a_new_process

HOST_FORMAT = "host %(levelname)s %(name)s %(message)s"


@pytest.fixture
def logger(caplog):
    caplog.set_level(logging.NOTSET, logger="hirefire_resource")
    return logging.getLogger("hirefire_resource")


def cut_off_from_every_handler(logger, monkeypatch):
    monkeypatch.setattr(logger, "propagate", False)


@pytest.fixture
def host_stream():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(HOST_FORMAT))
    logging.getLogger().addHandler(handler)
    yield stream
    logging.getLogger().removeHandler(handler)


def test_a_line_reaches_a_handler_of_the_host_in_its_format_and_is_not_printed(
    logger, host_stream, capsys
):
    logger.setLevel(logging.INFO)

    safe_log(logger, "info", "ready")

    assert host_stream.getvalue() == "host INFO hirefire_resource ready\n"
    assert capsys.readouterr().out == ""


def test_a_line_below_the_level_of_the_host_is_not_logged(logger, host_stream, capsys):
    logger.setLevel(logging.WARNING)

    safe_log(logger, "info", "ready")
    safe_log(logger, "warning", "careful")

    assert host_stream.getvalue() == "host WARNING hirefire_resource careful\n"
    assert capsys.readouterr().out == ""


def test_a_line_is_printed_to_stdout_when_the_host_has_no_handler(
    logger, monkeypatch, capsys
):
    cut_off_from_every_handler(logger, monkeypatch)

    safe_log(logger, "info", "ready")
    safe_log(logger, "error", "broken")

    captured = capsys.readouterr()
    assert captured.out == "ready\nbroken\n"
    assert captured.err == ""


def test_a_printed_line_goes_to_the_stdout_of_that_moment(logger, monkeypatch):
    cut_off_from_every_handler(logger, monkeypatch)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stdout", stream)

    safe_log(logger, "info", "ready")

    assert stream.getvalue() == "ready\n"


def test_a_level_on_the_logger_holds_a_printed_line_back(logger, monkeypatch, capsys):
    cut_off_from_every_handler(logger, monkeypatch)
    logger.setLevel(logging.WARNING)

    safe_log(logger, "info", "ready")
    safe_log(logger, "warning", "careful")

    assert capsys.readouterr().out == "careful\n"


def test_a_disabled_logger_prints_nothing(logger, monkeypatch, capsys):
    cut_off_from_every_handler(logger, monkeypatch)
    monkeypatch.setattr(logger, "disabled", True)

    safe_log(logger, "error", "broken")

    assert capsys.readouterr().out == ""


def test_a_bare_script_prints_the_lines_of_the_client_to_stdout():
    result = boot_in_a_new_process()

    assert STARTING in result.stdout.splitlines()
    assert "HireFire" not in result.stderr


def test_a_script_that_configures_logging_after_the_import_gets_the_lines_in_its_format():
    result = boot_in_a_new_process(
        "import logging\n"
        f"logging.basicConfig(level=logging.INFO, format={HOST_FORMAT!r})"
    )

    assert f"host INFO hirefire_resource {STARTING}" in result.stderr.splitlines()
    assert "HireFire" not in result.stdout


def test_a_script_that_logs_from_warning_up_gets_no_info_line():
    result = boot_in_a_new_process("import logging\nlogging.basicConfig()")

    assert "HireFire" not in result.stdout + result.stderr


def test_a_logging_configuration_that_disables_existing_loggers_keeps_the_lines():
    config = {
        "version": 1,
        "formatters": {"host": {"format": HOST_FORMAT}},
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "host"}
        },
        "root": {"level": "INFO", "handlers": ["console"]},
    }

    result = boot_in_a_new_process(
        f"import logging.config\nlogging.config.dictConfig({config!r})"
    )

    assert f"host INFO hirefire_resource {STARTING}" in result.stderr.splitlines()


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


@pytest.mark.parametrize(
    "text, expected",
    [
        ("redis://:p/ss@host:6379/0", "redis://***@host:6379/0"),
        ("rediss://:pa@ss@host:6379", "rediss://***@host:6379"),
        ("amqp://guest:gu%40est@host//", "amqp://***@host//"),
        ("no reply from redis://host:6379/0", "no reply from redis://host:6379/0"),
        ("redis://host:6379/0?name=a@b", "redis://host:6379/0?name=a@b"),
        ("http://host/path failed for a@b", "http://host/path failed for a@b"),
    ],
)
def test_format_error_hides_the_credentials_of_a_url_and_nothing_else(text, expected):
    assert format_error(RuntimeError(text)) == f"RuntimeError: {expected}"


def test_format_error_keeps_an_error_text_on_one_line():
    error = RuntimeError("bad\x00reply\r\n[HireFire] forged \u2028line")

    assert format_error(error) == (
        "RuntimeError: bad\\x00reply\\r\\n[HireFire] forged \\u2028line"
    )


def test_a_printed_line_is_flushed_at_once(logger, monkeypatch):
    class Stream(io.StringIO):
        flushes = 0

        def flush(self):
            self.flushes += 1

    cut_off_from_every_handler(logger, monkeypatch)
    stream = Stream()
    monkeypatch.setattr("sys.stdout", stream)

    safe_log(logger, "info", "ready")

    assert stream.getvalue() == "ready\n"
    assert stream.flushes == 1
