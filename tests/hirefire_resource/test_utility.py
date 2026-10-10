import pytest

from hirefire_resource.errors import MissingQueueError
from hirefire_resource.utility import (
    normalize_queues,
    redis_tls_options,
    round_half_up,
)


def test_normalizes_none_and_numbers_to_a_string_set():
    assert normalize_queues(None, 1, "mailer", allow_empty=False) == {"1", "mailer"}
    assert normalize_queues(None, allow_empty=True) == set()


def test_strips_surrounding_whitespace():
    assert normalize_queues(" default ", "default", "mailer", allow_empty=False) == {
        "default",
        "mailer",
    }


def test_normalize_queues_requires_allow_empty():
    with pytest.raises(TypeError):
        normalize_queues("default")


def test_empty_queues_disallowed_raises():
    with pytest.raises(
        MissingQueueError,
        match="No queue was specified. Please specify at least one queue.",
    ):
        normalize_queues(allow_empty=False)
    with pytest.raises(MissingQueueError):
        normalize_queues("  ", "", allow_empty=False)


def test_drops_blank_entries():
    assert normalize_queues("default", "  ", allow_empty=False) == {"default"}


def test_flattens_one_level_of_nested_lists():
    assert normalize_queues(["a", "b"], "c", allow_empty=False) == {"a", "b", "c"}


def test_empty_queues_allowed():
    assert normalize_queues(allow_empty=True) == set()
    assert normalize_queues("  ", allow_empty=True) == set()
    assert normalize_queues("  ", "", allow_empty=True) == set()


@pytest.mark.parametrize("url", ["rediss://host:6379/0", "REDISS://host:6379/0"])
def test_on_heroku_a_rediss_url_is_read_without_verifying_the_certificate(
    url, monkeypatch
):
    monkeypatch.setenv("DYNO", "worker.1")

    assert redis_tls_options(url) == {"ssl_cert_reqs": "none"}


def test_on_heroku_a_redis_url_without_tls_gets_no_tls_option(monkeypatch):
    monkeypatch.setenv("DYNO", "worker.1")

    assert redis_tls_options("redis://host:6379/0") == {}


def test_off_heroku_a_rediss_url_is_verified():
    assert redis_tls_options("rediss://host:6379/0") == {}


def test_round_half_up_rounds_a_half_up_at_the_digits_asked_for():
    assert round_half_up(2.5) == 3.0
    assert round_half_up(2.4) == 2.0
    assert round_half_up(2.25, 1) == 2.3
    assert round_half_up(0.125, 2) == 0.13
    assert isinstance(round_half_up(2.5), float)
