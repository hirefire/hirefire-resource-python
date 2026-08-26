import pytest

from hirefire_resource.errors import MissingQueueError
from hirefire_resource.utility import normalize_queues


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


def test_empty_queues_allowed():
    assert normalize_queues(allow_empty=True) == set()
    assert normalize_queues("  ", allow_empty=True) == set()
    assert normalize_queues("  ", "", allow_empty=True) == set()
