import pytest

from hirefire_resource.worker import InvalidDynoNameError, MissingDynoProcError, Worker


def test_worker_initialization_and_methods():
    worker = Worker("worker", lambda: 1.23)
    assert worker.name == "worker"
    assert worker.value() == 1.23


def test_invalid_dyno_name_error():
    with pytest.raises(InvalidDynoNameError):
        Worker("invalid name", lambda: 1.23)


def test_missing_dyno_proc_error():
    with pytest.raises(MissingDynoProcError):
        Worker("worker")
