import logging
from unittest.mock import patch

import pytest

from hirefire_resource import identity
from hirefire_resource.buffer import Buffer
from hirefire_resource.configuration import (
    Configuration,
    DuplicateDynoError,
    MissingSamplerError,
    UnexpectedSamplerError,
    UnknownCollectorError,
)
from hirefire_resource.cpu import CPU
from hirefire_resource.dispatcher import Dispatcher
from hirefire_resource.web import Web


@pytest.fixture
def config():
    return Configuration()


def test_default_logger(config):
    assert isinstance(config.logger, logging.Logger)
    assert config.logger.name == "hirefire_resource"
    assert any(isinstance(h, logging.StreamHandler) for h in config.logger.handlers)


def test_logger_does_not_propagate_to_root(config):
    # Own stdout handler, but no propagation, otherwise a host app with root
    # logging configured would see every HireFire line twice.
    assert config.logger.propagate is False


def test_can_set_logger(config):
    custom_logger = logging.getLogger("custom")
    config.logger = custom_logger
    assert config.logger is custom_logger


def test_web_defaults_to_none(config):
    assert config.web is None


def test_workers_default_to_empty(config):
    assert not config.workers.any()


def test_cpu_defaults_to_empty(config):
    assert config.cpu == []


def test_dyno_web_configures_http(config):
    config.dyno("web")
    assert isinstance(config.web, Web)
    assert config.web.name == "web"


def test_dyno_web_is_case_insensitive_for_http(config):
    config.dyno("Web")
    assert isinstance(config.web, Web)
    assert config.web.name == "Web"


def test_dyno_with_a_sampler_configures_a_worker(config):
    config.dyno("worker", lambda: 1.23)
    config.dyno("mailer", lambda: 2.46)
    workers = list(config.workers)
    assert len(workers) == 2
    assert workers[0].name == "worker"
    assert workers[0].sample() == 1.23
    assert workers[1].name == "mailer"
    assert workers[1].sample() == 2.46


def test_dyno_web_with_cpu_configures_cpu(config):
    config.dyno("web", tracking="cpu")
    assert config.web is None
    assert [collector.name for collector in config.cpu] == ["web"]


def test_dyno_non_web_with_cpu_configures_cpu(config):
    config.dyno("clock", tracking="cpu")
    assert len(config.cpu) == 1
    assert isinstance(config.cpu[0], CPU)
    assert config.cpu[0].name == "clock"


def test_dyno_without_sampler_or_tracking_raises_for_a_non_web_name(config):
    with pytest.raises(MissingSamplerError):
        config.dyno("worker")


def test_dyno_rejects_http_family_acronyms(config):
    with pytest.raises(UnknownCollectorError):
        config.dyno("web", tracking="rqt")


def test_dyno_rejects_the_http_keyword(config):
    with pytest.raises(UnknownCollectorError):
        config.dyno("web", tracking="http")


def test_dyno_rejects_job_family_acronyms(config):
    with pytest.raises(UnknownCollectorError):
        config.dyno("worker", lambda: 1, tracking="jql")


def test_dyno_cpu_rejects_a_sampler(config):
    with pytest.raises(UnexpectedSamplerError):
        config.dyno("web", lambda: 1, tracking="cpu")


def test_service_http_configures_http(config):
    config.service("web", tracking="http")
    assert isinstance(config.web, Web)
    assert config.web.name == "web"


def test_service_http_allows_a_non_web_name(config):
    config.service("api", tracking="http")
    assert config.web.name == "api"


def test_service_with_a_sampler_configures_a_worker(config):
    config.service("worker", lambda: 1.23)
    workers = list(config.workers)
    assert len(workers) == 1
    assert workers[0].name == "worker"
    assert workers[0].sample() == 1.23


def test_service_cpu_configures_cpu(config):
    config.service("clock", tracking="cpu")
    assert [collector.name for collector in config.cpu] == ["clock"]


def test_service_with_keyword_and_sampler_raises(config):
    with pytest.raises(UnexpectedSamplerError):
        config.service("web", lambda: 1, tracking="http")


def test_service_cpu_with_sampler_raises(config):
    with pytest.raises(UnexpectedSamplerError):
        config.service("clock", lambda: 1, tracking="cpu")


def test_service_without_keyword_or_sampler_raises(config):
    with pytest.raises(MissingSamplerError):
        config.service("worker")


def test_service_rejects_an_unknown_keyword(config):
    with pytest.raises(UnknownCollectorError):
        config.service("web", tracking="foo")


def test_empty_name_raises(config):
    with pytest.raises(ValueError):
        config.dyno(None, tracking="cpu")
    with pytest.raises(ValueError):
        config.dyno("", tracking="cpu")
    with pytest.raises(ValueError):
        config.service(None, tracking="http")
    with pytest.raises(ValueError):
        config.service("", tracking="http")


def test_duplicate_name_raises(config):
    config.dyno("web")
    with pytest.raises(DuplicateDynoError):
        config.dyno("web", tracking="cpu")


def test_duplicate_name_guard_spans_dyno_and_service_case_insensitively(config):
    config.dyno("web")
    with pytest.raises(DuplicateDynoError) as exc_info:
        config.service("Web", tracking="http")
    assert "Web" in str(exc_info.value)


def test_duplicate_name_check_is_case_insensitive(config):
    config.dyno("worker", lambda: 1)
    with pytest.raises(DuplicateDynoError):
        config.dyno("Worker", tracking="cpu")


def test_second_http_declaration_under_a_different_name_raises(config):
    config.dyno("web")
    with pytest.raises(DuplicateDynoError) as exc_info:
        config.service("api", tracking="http")
    assert "web" in str(exc_info.value)


def test_dyno_and_service_share_the_one_http_guard(config):
    config.service("api", tracking="http")
    with pytest.raises(DuplicateDynoError):
        config.dyno("web")


def test_rejected_declaration_does_not_reserve_the_name(config):
    with pytest.raises(UnexpectedSamplerError):
        config.service("web", lambda: 1, tracking="http")

    config.service("web", tracking="http")
    assert config.web.name == "web"


def test_dyno_and_service_register_into_the_same_collectors(config, monkeypatch):
    config.dyno("web")
    config.service("worker", lambda: 1)
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "clock")
    config.service("clock", tracking="cpu")

    assert config.web.name == "web"
    assert [worker.name for worker in config.workers] == ["worker"]
    assert [collector.name for collector in config.cpu] == ["clock"]


def test_dispatcher_returns_instance(config):
    assert isinstance(config.dispatcher, Dispatcher)


def test_dispatcher_is_memoized(config):
    assert config.dispatcher is config.dispatcher


def test_dispatcher_receives_web(config):
    config.dyno("web")
    assert config.dispatcher._web is config.web


def test_dispatcher_receives_workers(config):
    config.dyno("worker", lambda: 1)
    assert config.dispatcher._workers is config.workers


def test_dispatcher_without_web(config):
    assert config.dispatcher._web is None


def test_buffer_returns_instance(config):
    assert isinstance(config.buffer, Buffer)


def test_buffer_is_memoized(config):
    assert config.buffer is config.buffer


def test_cpu_collector_active_when_identity_matches(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "clock")
    config.dyno("clock", tracking="cpu")
    active = config.dispatcher._cpu
    assert [collector.name for collector in active] == ["clock"]


def test_cpu_collector_dormant_when_identity_differs(config, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    config.dyno("clock", tracking="cpu")
    assert config.dispatcher._cpu == []


def test_cpu_collector_disabled_and_logged_when_identity_unresolved(config, caplog):
    caplog.set_level(logging.ERROR)
    config.dyno("clock", tracking="cpu")
    assert config.dispatcher._cpu == []
    assert "HIREFIRE_SERVICE_NAME" in caplog.text


def test_identity_resolution_skipped_with_only_job_collectors(config, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    with patch.object(identity, "resolve") as mock_resolve:
        config.dyno("worker", lambda: 1)
        config.dispatcher
        mock_resolve.assert_not_called()


def test_web_liveness_allowed_when_identity_matches(config, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    config.dyno("web")
    assert config.dispatcher._web_liveness


def test_web_liveness_allowed_when_identity_unresolved(config):
    config.dyno("web")
    assert config.dispatcher._web_liveness


def test_web_liveness_denied_when_identity_differs(config, monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")
    config.dyno("web")
    assert not config.dispatcher._web_liveness


def test_web_liveness_matches_non_web_http_names(config, monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_NAME", "api")
    config.service("api", tracking="http")
    assert config.dispatcher._web_liveness


def test_web_liveness_matches_case_insensitively(config, monkeypatch):
    monkeypatch.setenv("DYNO", "Web.1")
    config.dyno("web")
    assert config.dispatcher._web_liveness


def test_cpu_collector_matches_case_insensitively(config, monkeypatch):
    monkeypatch.setenv("DYNO", "Worker.1")
    config.dyno("worker", tracking="cpu")
    active = config.dispatcher._cpu
    assert [collector.name for collector in active] == ["worker"]


def test_heroku_config_var_conflict_is_warned(config, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("DYNO", "worker.1")
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    config.dyno("worker", tracking="cpu")
    config.dispatcher
    assert "app-wide" in caplog.text


def test_web_liveness_true_without_a_web_collector(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "clock")
    config.dyno("clock", tracking="cpu")
    assert config.dispatcher._web_liveness


def test_heroku_config_var_conflict_warned_only_once(config, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("DYNO", "worker.1")
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")

    config.dyno("web")  # web_liveness resolves identity
    config.dyno("clock", tracking="cpu")  # active_cpu_collectors resolves identity too

    config.dispatcher  # both gates run, but resolution (and the warning) is memoized

    assert caplog.text.count("app-wide") == 1


def test_log_queue_metrics_defaults_to_false(config):
    assert not config.log_queue_metrics


def test_log_queue_metrics_can_be_set(config):
    config.log_queue_metrics = True
    assert config.log_queue_metrics


def test_token_defaults_to_env(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "from-env")
    assert config.token == "from-env"


def test_token_can_be_overridden(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "from-env")
    config.token = "custom-token"
    assert config.token == "custom-token"


def test_token_defaults_to_none_without_env(config):
    assert config.token is None


# tracking is keyword-only. The second positional argument is the sampler, which
# keeps the 1.x dyno("worker", callable) form working.


def test_tracking_cannot_be_passed_positionally(config):
    with pytest.raises(TypeError):
        config.dyno("clock", None, "cpu")
    with pytest.raises(TypeError):
        config.service("web", None, "http")
