import pytest

from hirefire_resource.errors import MissingQueueError
from hirefire_resource.utility import normalize_queues


def test_normalize_queues_trims_and_dedupes():
    assert normalize_queues(" default ", "default", "mailer", allow_empty=False) == {
        "default",
        "mailer",
    }


def test_normalize_queues_requires_allow_empty():
    with pytest.raises(TypeError):
        normalize_queues("default")


def test_normalize_queues_rejects_blank_when_required():
    with pytest.raises(
        MissingQueueError,
        match="No queue was specified. Please specify at least one queue.",
    ):
        normalize_queues(allow_empty=False)
    with pytest.raises(MissingQueueError):
        normalize_queues("  ", "", allow_empty=False)


def test_normalize_queues_drops_blank_entries_mixed_with_real_names():
    assert normalize_queues("default", "  ", allow_empty=False) == {"default"}


def test_normalize_queues_allows_empty_when_requested():
    assert normalize_queues(allow_empty=True) == set()
    assert normalize_queues("  ", allow_empty=True) == set()
    assert normalize_queues("  ", "", allow_empty=True) == set()
