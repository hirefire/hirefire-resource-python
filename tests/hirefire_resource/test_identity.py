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
    # One trailing "-<alnum>" segment is not a Fir pod suffix (needs two).
    monkeypatch.setenv("DYNO", "worker-abc123")
    assert identity.resolve() == "worker-abc123"
