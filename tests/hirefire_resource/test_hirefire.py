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
        with HireFire.configure() as config:
            config.dyno("web")
        mock_start.assert_called_once()


def test_configure_does_not_start_dispatcher_without_token():
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
        mock_start.assert_not_called()


def test_reset_stops_dispatcher_and_replaces_configuration():
    configuration = HireFire.configuration
    dispatcher = configuration.dispatcher

    with patch.object(dispatcher, "stop") as mock_stop:
        HireFire.reset()
        mock_stop.assert_called_once()

    assert HireFire.configuration is not configuration
