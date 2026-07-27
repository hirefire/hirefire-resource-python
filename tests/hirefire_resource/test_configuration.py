import logging

import pytest

from hirefire_resource.buffer import Buffer
from hirefire_resource.configuration import (
    Configuration,
    DuplicateDynoError,
    MissingSamplerError,
)
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
    assert config.logger.propagate is False


def test_can_set_logger(config):
    custom_logger = logging.getLogger("custom")
    config.logger = custom_logger
    assert config.logger is custom_logger


def test_http_defaults_to_none(config):
    assert config.http is None
    assert config.web is None


def test_job_queues_default_to_empty(config):
    assert not config.job_queues.any()
    assert not config.workers.any()


def test_dyno_web_configures_http(config):
    config.dyno("web")
    assert isinstance(config.http, Web)
    assert config.http.name == "web"


def test_dyno_web_is_case_insensitive_for_http(config):
    config.dyno("Web")
    assert isinstance(config.http, Web)
    assert config.http.name == "Web"


def test_dyno_with_a_sampler_configures_a_job_queue(config):
    config.dyno("worker", lambda: 1.23)
    config.dyno("mailer", lambda: 2.46)
    workers = list(config.job_queues)
    assert len(workers) == 2
    assert workers[0].name == "worker"
    assert workers[0].sample() == 1.23
    assert workers[1].name == "mailer"
    assert workers[1].sample() == 2.46


def test_dyno_without_sampler_raises_for_a_non_web_name(config):
    with pytest.raises(MissingSamplerError) as exc:
        config.dyno("worker")
    assert "needs a sampler" in str(exc.value)
    assert "tracking" not in str(exc.value).lower() or "service" not in str(exc.value)


def test_dyno_rejects_tracking_keyword(config):
    with pytest.raises(TypeError):
        config.dyno("web", tracking="cpu")  # type: ignore[call-arg]


def test_multi_kind_same_name_allowed(config):
    config.dyno("web")
    config.dyno("web", lambda: 1)
    assert config.http is not None
    assert config.job_queues.any()


def test_duplicate_http_rejected(config):
    config.dyno("web")
    with pytest.raises(DuplicateDynoError):
        config.dyno("Web")


def test_duplicate_job_queue_rejected(config):
    config.dyno("worker", lambda: 1)
    with pytest.raises(DuplicateDynoError):
        config.dyno("Worker", lambda: 2)


def test_rejected_registration_does_not_reserve_name(config):
    with pytest.raises(MissingSamplerError):
        config.dyno("worker")
    config.dyno("worker", lambda: 1)
    assert list(config.job_queues)[0].name == "worker"


def test_empty_name_raises(config):
    with pytest.raises(ValueError):
        config.dyno("")
    with pytest.raises(ValueError):
        config.dyno("   ")


def test_name_max_bytes(config):
    with pytest.raises(ValueError):
        config.dyno("w" * 129)


def test_name_strips(config):
    config.dyno("  web  ")
    assert config.http.name == "web"


def test_token_from_env(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "from-env")
    assert config.token == "from-env"


def test_token_override(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "from-env")
    config.token = "from-code"
    assert config.token == "from-code"


def test_token_empty_env_absent(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "")
    assert config.token is None


def test_token_empty_assignment_force_off(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "from-env")
    config.token = ""
    assert config.token is None


def test_token_nil_clears_to_env(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "from-env")
    config.token = "override"
    config.token = None
    assert config.token == "from-env"


def test_token_strips_whitespace(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "  abc  ")
    assert config.token == "abc"
    config.token = "  def  \n"
    assert config.token == "def"


def test_token_whitespace_only_absent(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_TOKEN", "   ")
    assert config.token is None


def test_soft_identity_re_resolves(config, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    assert config.soft_identity() == "web"
    monkeypatch.setenv("DYNO", "worker.1")
    assert config.soft_identity() == "worker"


def test_soft_identity_too_long(config, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "w" * 129)
    assert config.soft_identity() is None
    assert "exceeds 128 bytes" in caplog.text


def test_rqt_enabled_platform_role(config, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    assert config.rqt_enabled()
    assert config.rqt_liveness()


def test_rqt_enabled_render_role(config, monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_NAME", "api")
    monkeypatch.setenv("RENDER_SERVICE_TYPE", "web")
    assert config.rqt_enabled()
    assert config.rqt_liveness()


def test_rqt_not_armed_by_service_name_alone(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    monkeypatch.setenv("DYNO", "worker.1")
    assert not config.rqt_enabled()


def test_rqt_enabled_explicit_http(config):
    config.dyno("web")
    assert config.rqt_enabled()
    assert not config.rqt_liveness()


def test_rqt_enabled_mark_http_active(config, monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")
    config.mark_http_active()
    assert config.rqt_enabled()
    assert config.rqt_liveness()
    assert config.http_name() == "worker"


def test_rqt_liveness_requires_identity_match(config, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    config.dyno("web")
    assert config.rqt_liveness()


def test_rqt_liveness_false_when_identity_unresolved(config):
    config.dyno("web")
    assert config.rqt_enabled()
    assert not config.rqt_liveness()
    assert config.http_source() is not None


def test_http_source_always_on_rebuilds_on_name_change(config, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    first = config.http_source()
    assert first is not None
    assert first.name == "web"
    monkeypatch.setenv("DYNO", "api.1")
    second = config.http_source()
    assert second is not None
    assert second.name == "api"
    assert second is not first


def test_active_cpu_sources_always_on(config, monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "clock")
    sources = config.active_cpu_sources()
    assert len(sources) == 1
    assert sources[0].name == "clock"


def test_active_cpu_sources_unresolved(config, caplog):
    caplog.set_level(logging.WARNING)
    assert config.active_cpu_sources() == []
    assert "CPU metrics disabled" in caplog.text


def test_reset_after_fork_clears_always_on(config, monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    assert config.http_source() is not None
    assert config.active_cpu_sources()
    config.reset_after_fork()
    assert config._always_on_http is None
    assert config._always_on_cpu is None


def test_prefork_web_handoff_matches_rqt_enabled(config, monkeypatch):
    assert not config.prefork_web_handoff()
    monkeypatch.setenv("DYNO", "web.1")
    assert config.prefork_web_handoff()


def test_buffer_and_dispatcher_lazy(config):
    assert isinstance(config.buffer, Buffer)
    assert isinstance(config.dispatcher, Dispatcher)
    assert config.dispatcher is config.dispatcher


def test_stop_dispatcher_flush_forwarded(config):
    dispatcher = config.dispatcher
    called = {}

    def stop(*, flush=True):
        called["flush"] = flush
        return True

    dispatcher.stop = stop  # type: ignore[method-assign]
    config.stop_dispatcher(flush=False)
    assert called["flush"] is False


def test_rqt_liveness_denied_when_identity_differs(config, monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")
    config.dyno("web")
    assert not config.rqt_liveness()


def test_rqt_liveness_false_when_armed_but_identity_unresolved(config):
    config.mark_http_active()
    assert config.rqt_enabled()
    assert not config.rqt_liveness()
    assert config.http_source() is None


def test_canonical_name_preserves_first_seen_casing(config):
    config.dyno("Web")
    config.dyno("web", lambda: 1)
    assert config.http is not None
    assert config.http.name == "Web"
    assert config.job_queues.find_by_name("Web") is not None


def test_rqt_not_enabled_for_render_worker_without_traffic(config, monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_NAME", "worker")
    monkeypatch.setenv("RENDER_SERVICE_TYPE", "worker")
    assert not config.rqt_enabled()


def test_rqt_liveness_matches_case_insensitively(config, monkeypatch):
    monkeypatch.setenv("DYNO", "Web.1")
    config.dyno("web")
    assert config.rqt_liveness()


def test_heroku_config_var_conflict_is_warned(config, monkeypatch, caplog):
    monkeypatch.setenv("DYNO", "worker.1")
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    caplog.set_level(logging.WARNING)
    config.active_cpu_sources()
    assert "app-wide" in caplog.text


def test_heroku_config_var_conflict_warned_only_once(config, monkeypatch, caplog):
    monkeypatch.setenv("DYNO", "worker.1")
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    caplog.set_level(logging.WARNING)
    config.dyno("web")
    config.active_cpu_sources()
    config.rqt_liveness()
    assert caplog.text.count("app-wide") == 1
