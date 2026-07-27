from hirefire_resource import identity


def test_resolves_to_none_when_nothing_is_set():
    assert identity.resolve() is None


def test_explicit_service_name_wins(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "clock")
    monkeypatch.setenv("DYNO", "web.1")
    monkeypatch.setenv("RENDER_SERVICE_NAME", "api")
    assert identity.resolve() == "clock"


def test_falls_back_to_heroku_dyno_prefix(monkeypatch):
    monkeypatch.setenv("DYNO", "worker.42")
    assert identity.resolve() == "worker"


def test_resolves_fir_pod_names(monkeypatch):
    monkeypatch.setenv("DYNO", "web-5fb9c979-lft2l")
    assert identity.resolve() == "web"


def test_resolves_fir_pod_names_with_mixed_case_suffixes(monkeypatch):
    monkeypatch.setenv("DYNO", "web-12A34B56D-E78F9")
    assert identity.resolve() == "web"


def test_resolves_fir_pod_names_with_underscores(monkeypatch):
    monkeypatch.setenv("DYNO", "worker_latency-6d7f788ddb-cdct6")
    assert identity.resolve() == "worker_latency"


def test_fir_pod_name_preserves_dashes_inside_the_process_name(monkeypatch):
    monkeypatch.setenv("DYNO", "my-worker-5fb9c979-lft2l")
    assert identity.resolve() == "my-worker"


def test_falls_back_to_render_service_name(monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_NAME", "background-worker")
    assert identity.resolve() == "background-worker"


def test_blank_values_are_ignored(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "")
    monkeypatch.setenv("DYNO", "web.1")
    assert identity.resolve() == "web"


def test_strips_whitespace_from_identity_env(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "  clock  \n")
    assert identity.resolve() == "clock"

    monkeypatch.delenv("HIREFIRE_SERVICE_NAME", raising=False)
    monkeypatch.setenv("DYNO", "  worker.1  ")
    assert identity.resolve() == "worker"

    monkeypatch.delenv("DYNO", raising=False)
    monkeypatch.setenv("RENDER_SERVICE_NAME", "\tapi\t")
    assert identity.resolve() == "api"


def test_whitespace_only_identity_env_is_absent(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "   ")
    monkeypatch.setenv("DYNO", "web.1")
    assert identity.resolve() == "web"


def test_heroku_conflict_when_explicit_disagrees_with_dyno_prefix(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    monkeypatch.setenv("DYNO", "worker.1")
    assert identity.heroku_conflict()


def test_no_heroku_conflict_when_they_agree(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "worker")
    monkeypatch.setenv("DYNO", "worker.1")
    assert not identity.heroku_conflict()


def test_no_heroku_conflict_without_dyno(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    assert not identity.heroku_conflict()


def test_no_heroku_conflict_when_names_differ_only_in_case(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "Worker")
    monkeypatch.setenv("DYNO", "worker.1")
    assert not identity.heroku_conflict()


def test_heroku_dyno_takes_precedence_over_render_service_name(monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")
    monkeypatch.setenv("RENDER_SERVICE_NAME", "api")
    assert identity.resolve() == "worker"


def test_dyno_name_without_a_suffix_is_returned_as_is(monkeypatch):
    monkeypatch.setenv("DYNO", "web")
    assert identity.resolve() == "web"


def test_dyno_name_with_a_single_trailing_segment_is_preserved(monkeypatch):
    monkeypatch.setenv("DYNO", "worker-abc123")
    assert identity.resolve() == "worker-abc123"


def test_dyno_that_strips_to_empty_is_unresolved(monkeypatch):
    monkeypatch.setenv("DYNO", ".1")
    assert identity.resolve() is None

    monkeypatch.setenv("DYNO", "-ab-cd")
    assert identity.resolve() is None


def test_platform_http_role_heroku_cedar_web(monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    assert identity.heroku_web_process()
    assert identity.platform_http_role()


def test_platform_http_role_heroku_fir_web(monkeypatch):
    monkeypatch.setenv("DYNO", "web-5fb9c979-lft2l")
    assert identity.heroku_web_process()
    assert identity.platform_http_role()


def test_platform_http_role_heroku_worker_is_not_web(monkeypatch):
    for dyno in ("worker.1", "worker.42"):
        monkeypatch.setenv("DYNO", dyno)
        assert not identity.heroku_web_process()
        assert not identity.platform_http_role()


def test_platform_http_role_uses_dyno_not_explicit_service_name(monkeypatch):
    monkeypatch.setenv("HIREFIRE_SERVICE_NAME", "web")
    monkeypatch.setenv("DYNO", "worker.1")
    assert not identity.platform_http_role()


def test_platform_http_role_render_web_service_type(monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_NAME", "api")
    monkeypatch.setenv("RENDER_SERVICE_TYPE", "web")
    assert identity.render_web_service()
    assert identity.platform_http_role()


def test_platform_http_role_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("DYNO", "Web.1")
    assert identity.platform_http_role()

    monkeypatch.delenv("DYNO", raising=False)
    monkeypatch.setenv("RENDER_SERVICE_NAME", "api")
    monkeypatch.setenv("RENDER_SERVICE_TYPE", "Web")
    assert identity.platform_http_role()


def test_platform_http_role_render_worker_type(monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_NAME", "worker")
    monkeypatch.setenv("RENDER_SERVICE_TYPE", "worker")
    assert not identity.render_web_service()
    assert not identity.platform_http_role()


def test_platform_http_role_render_pserv_is_not_web_role(monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_TYPE", "pserv")
    assert not identity.platform_http_role()


def test_platform_http_role_false_with_no_platform_env():
    assert not identity.platform_http_role()
    assert not identity.heroku_web_process()
    assert not identity.render_web_service()


def test_heroku_web_process_rejects_names_that_only_start_with_web(monkeypatch):
    for dyno in ("webworker.1", "webbing.1", "web_service.1"):
        monkeypatch.setenv("DYNO", dyno)
        assert not identity.heroku_web_process()
        assert not identity.platform_http_role()


def test_heroku_web_process_rejects_fir_worker(monkeypatch):
    monkeypatch.setenv("DYNO", "worker-12a34b56d-e78f9")
    assert not identity.heroku_web_process()
    assert not identity.platform_http_role()


def test_heroku_conflict_false_when_only_dyno_present(monkeypatch):
    monkeypatch.setenv("DYNO", "web.1")
    assert not identity.heroku_conflict()


def test_render_service_type_blank_is_not_web_role(monkeypatch):
    monkeypatch.setenv("RENDER_SERVICE_NAME", "api")
    monkeypatch.setenv("RENDER_SERVICE_TYPE", "   ")
    assert not identity.render_web_service()
    assert not identity.platform_http_role()
