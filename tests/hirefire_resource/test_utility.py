import pytest

from hirefire_resource.errors import MissingQueueError
from hirefire_resource.utility import normalize_queues


def test_normalize_queues_trims_and_dedupes():
    assert normalize_queues(" default ", "default", "mailer") == {"default", "mailer"}


def test_normalize_queues_rejects_blank_when_required():
    with pytest.raises(MissingQueueError):
        normalize_queues()
    with pytest.raises(MissingQueueError):
        normalize_queues("  ", "")


def test_normalize_queues_allows_empty_when_requested():
    assert normalize_queues(allow_empty=True) == set()
    assert normalize_queues("  ", allow_empty=True) == set()
