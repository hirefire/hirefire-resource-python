import importlib
import logging
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hirefire_resource import HireFire, host
from hirefire_resource.configuration import Configuration
from hirefire_resource.dispatcher import Dispatcher
from tests.helpers import fake_macro, set_HIREFIRE_TOKEN  # noqa: F401


def test_default_configuration():
    assert isinstance(HireFire.configuration, Configuration)


def test_configure_yields_configuration():
    with HireFire.configure() as config:
        configuration = config
    assert configuration is HireFire.configuration


def test_configure_starts_dispatcher_when_token_is_set(set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        mock_start.assert_called_once()


def test_boot_is_configure_with_empty_callable(set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start") as mock_start:
        assert HireFire.boot() is HireFire.configuration
        mock_start.assert_called_once()


def test_configure_does_not_start_dispatcher_without_token():
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        mock_start.assert_not_called()


def test_configure_does_not_start_dispatcher_with_empty_token(monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "")
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        mock_start.assert_not_called()


def test_configure_does_not_start_dispatcher_when_token_is_forced_empty(
    monkeypatch,
):
    monkeypatch.setenv("HIREFIRE_TOKEN", "from-env")
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.token = ""
            config.dyno("web")
        mock_start.assert_not_called()


def test_a_reset_leaves_the_level_of_the_logger_as_the_host_set_it(caplog):
    caplog.set_level(logging.WARNING, logger="hirefire_resource")

    HireFire.reset()

    assert logging.getLogger("hirefire_resource").level == logging.WARNING
    assert logging.getLogger("hirefire_resource").handlers == []


def test_reset_stops_dispatcher_and_replaces_configuration():
    configuration = HireFire.configuration
    dispatcher = configuration.dispatcher

    with patch.object(dispatcher, "stop") as mock_stop:
        HireFire.reset()
        mock_stop.assert_called_once()

    assert HireFire.configuration is not configuration


def test_boot_without_token_does_not_start_dispatcher():
    with patch.object(Dispatcher, "start") as mock_start:
        HireFire.boot()
        mock_start.assert_not_called()


def test_configure_token_assignment_starts_dispatcher():
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.token = "inline-token"
            config.dyno("worker", lambda: 1)
        mock_start.assert_called_once()


@pytest.mark.parametrize("dyno", ["run.4821", "release.7310", "RUN.1"])
def test_configure_does_not_start_the_dispatcher_in_a_one_off_dyno(
    dyno, monkeypatch, set_HIREFIRE_TOKEN
):
    monkeypatch.setenv("DYNO", dyno)

    with patch.object(Dispatcher, "start") as start:
        with HireFire.configure() as config:
            config.dyno(dyno, lambda: 1)
        HireFire.boot()
        start.assert_not_called()


def test_boot_attaches_the_macro_of_every_loaded_job_library(
    set_HIREFIRE_TOKEN, load_macros
):
    attached = []
    load_macros(celery=fake_macro(attach=lambda: attached.append("celery")))

    with patch.object(Dispatcher, "start"):
        HireFire.boot()

    assert attached == ["celery"]


def test_boot_in_a_one_off_dyno_attaches_the_macros_and_starts_nothing(
    monkeypatch, set_HIREFIRE_TOKEN, load_macros
):
    monkeypatch.setenv("DYNO", "run.4821")
    attached = []
    load_macros(celery=fake_macro(attach=lambda: attached.append("celery")))

    with patch.object(Dispatcher, "start") as start:
        HireFire.boot()

    assert attached == ["celery"]
    start.assert_not_called()


def test_boot_without_a_token_attaches_nothing(load_macros):
    attached = []
    load_macros(celery=fake_macro(attach=lambda: attached.append("celery")))

    HireFire.boot()

    assert attached == []


def test_configure_does_not_start_in_a_one_off_dyno_that_carries_an_app_wide_service_name(
    monkeypatch, set_HIREFIRE_TOKEN
):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    monkeypatch.setenv("DYNO", "run.4821")

    with patch.object(Dispatcher, "start") as start:
        HireFire.boot()
        start.assert_not_called()


@pytest.mark.parametrize("dyno", ["web.1", "worker.3", "scheduler.5512", "runner.1"])
def test_configure_starts_the_dispatcher_in_every_other_dyno(
    dyno, monkeypatch, set_HIREFIRE_TOKEN
):
    monkeypatch.setenv("DYNO", dyno)

    with patch.object(Dispatcher, "start") as start:
        HireFire.boot()
        start.assert_called_once()


def test_boot_in_a_uwsgi_parent_starts_nothing_and_warns_once(
    monkeypatch, set_HIREFIRE_TOKEN, caplog
):
    monkeypatch.setattr(host, "uwsgi", SimpleNamespace(worker_id=lambda: 0))

    with patch.object(Dispatcher, "start") as start:
        HireFire.boot()
        HireFire.boot()
        start.assert_not_called()

    assert caplog.text.count("Run uWSGI with --lazy-apps") == 1
    assert caplog.records[0].levelname == "WARNING"


def test_boot_in_a_uwsgi_worker_starts_the_dispatcher(
    monkeypatch, set_HIREFIRE_TOKEN, caplog
):
    monkeypatch.setattr(host, "uwsgi", SimpleNamespace(worker_id=lambda: 2))

    with patch.object(Dispatcher, "start") as start:
        HireFire.boot()
        start.assert_called_once()

    assert "uWSGI" not in caplog.text


def test_boot_outside_uwsgi_does_not_ask_for_a_worker_id(set_HIREFIRE_TOKEN):
    assert host.uwsgi is None
    assert not host.under_uwsgi()
    assert not host.uwsgi_parent()


def test_a_uwsgi_parent_without_a_token_is_not_warned(monkeypatch, caplog):
    monkeypatch.setattr(host, "uwsgi", SimpleNamespace(worker_id=lambda: 0))

    HireFire.boot()

    assert "uWSGI" not in caplog.text


def test_the_uwsgi_module_that_the_server_embeds_is_found_at_import():
    embedded = SimpleNamespace(worker_id=lambda: 0)
    try:
        with patch.dict(sys.modules, {"uwsgi": embedded}):
            importlib.reload(host)
            assert host.uwsgi is embedded
            assert host.uwsgi_parent() is True
    finally:
        importlib.reload(host)

    assert host.under_uwsgi() is False
