from unittest.mock import patch

import pytest

from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration
from hirefire_resource.dispatcher import Dispatcher
from tests.helpers import set_HIREFIRE_TOKEN  # noqa: F401


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
        HireFire.boot()
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
