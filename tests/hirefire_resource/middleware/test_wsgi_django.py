import time
from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import re_path
from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.dispatcher import Dispatcher
from tests.helpers import (  # noqa: F401
    STARTING,
    boot_in_a_new_process,
    set_HIREFIRE_TOKEN,
)


def catch_all(request):
    return HttpResponse("DEFAULT")


urlpatterns = [re_path(r"^.*$", catch_all)]


@pytest.fixture(autouse=True)
def app():
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["testserver"],
        MIDDLEWARE=["hirefire_resource.middleware.wsgi.django.HireFireMiddleware"],
    ):
        yield


@pytest.fixture
def client():
    return Client()


def test_pass_through_without_token(client):
    with patch.object(Dispatcher, "start") as mock_start:
        with HireFire.configure() as config:
            config.dyno("web")
            config.dyno("worker", lambda: 1.23)
        response = client.get(
            "/any", HTTP_X_REQUEST_START=str(int(time.time() * 1000 - 5))
        )
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        mock_start.assert_not_called()


@freeze_time("2000-01-01 00:00:00")
def test_samples_without_web_dyno_via_identity(client, set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start") as mock_start:
        response = client.get(
            "/any", HTTP_X_REQUEST_START=str(int(time.time() * 1000 - 5))
        )
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        _ts = int(time.time())
        assert HireFire.configuration.buffer.flush()["web"]["rqt"] == {
            _ts: {"sum": float(5), "count": 1}
        }
        mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
def test_samples_web_and_starts_dispatcher(client, set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start") as mock_start:
        response = client.get(
            "/any", HTTP_X_REQUEST_START=str(int(time.time() * 1000 - 5))
        )
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        _ts = int(time.time())
        assert HireFire.configuration.buffer.flush()["web"]["rqt"] == {
            _ts: {"sum": float(5), "count": 1}
        }
        mock_start.assert_called()


@freeze_time("2000-01-01 00:00:00")
def test_falls_back_to_x_queue_start(client, set_HIREFIRE_TOKEN, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(Dispatcher, "start"):
        response = client.get(
            "/any", HTTP_X_QUEUE_START=str(int(time.time() * 1000 - 5))
        )
        assert response.status_code == 200
        assert response.content.decode("utf-8") == "DEFAULT"
        _ts = int(time.time())
        assert HireFire.configuration.buffer.flush()["web"]["rqt"] == {
            _ts: {"sum": float(5), "count": 1}
        }


DJANGO_WITH_LOGGING = (
    "import django\n"
    "from django.conf import settings\n"
    "settings.configure(LOGGING={logging!r})\n"
    "django.setup()"
)


def test_under_the_default_logging_of_django_the_lines_are_printed_to_stdout():
    result = boot_in_a_new_process(DJANGO_WITH_LOGGING.format(logging={}))

    assert STARTING in result.stdout.splitlines()
    assert "HireFire" not in result.stderr


def test_under_a_django_with_a_root_handler_the_lines_take_its_format():
    logging = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"host": {"format": "host %(levelname)s %(message)s"}},
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "host"}
        },
        "root": {"handlers": ["console"], "level": "INFO"},
    }

    result = boot_in_a_new_process(DJANGO_WITH_LOGGING.format(logging=logging))

    assert f"host INFO {STARTING}" in result.stderr.splitlines()
    assert "HireFire" not in result.stdout
