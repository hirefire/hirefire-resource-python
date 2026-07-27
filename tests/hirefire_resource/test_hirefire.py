from unittest.mock import patch

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
        with patch.object(Dispatcher, "ensure_job_queue_loop") as mock_ensure:
            with HireFire.configure() as config:
                config.dyno("web")
            mock_start.assert_called_once()
            mock_ensure.assert_called_once()


def test_boot_starts_when_token_present(set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start") as mock_start:
        with patch.object(Dispatcher, "ensure_job_queue_loop") as mock_ensure:
            HireFire.boot()
            mock_start.assert_called_once()
            mock_ensure.assert_called_once()


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


def test_additive_configure_after_boot_starts_worker_loop(set_HIREFIRE_TOKEN):
    with patch.object(Dispatcher, "start", return_value=True):
        with patch.object(Dispatcher, "ensure_job_queue_loop") as ensure1:
            HireFire.boot()
            ensure1.assert_called()

    with patch.object(Dispatcher, "start", return_value=False):
        with patch.object(Dispatcher, "ensure_job_queue_loop") as ensure2:
            with HireFire.configure() as config:
                config.dyno("worker", lambda: 42)
            ensure2.assert_called()


def test_configure_token_assignment_starts_dispatcher_and_job_queue_loop():
    with patch.object(Dispatcher, "start") as mock_start:
        with patch.object(Dispatcher, "ensure_job_queue_loop") as mock_ensure:
            with HireFire.configure() as config:
                config.token = "inline-token"
                config.dyno("worker", lambda: 1)
            mock_start.assert_called_once()
            mock_ensure.assert_called_once()
