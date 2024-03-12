import pytest

from hirefire_resource.worker import InvalidDynoNameError, MissingDynoProcError, Worker


def test_worker_valid_initialization():
    valid_names = [
        "worker",
        "worker1",
        "my-worker",
        "my_worker",
        "Worker_123",
        "worker-123",
        "w",
        "a" * 30,
    ]

    for name in valid_names:
        worker = Worker(name, lambda: 1.23)
        assert worker.name == name
        assert worker.value() == 1.23


def test_worker_invalid_name_raises_error():
    invalid_names = [
        "",  # Empty string
        "1worker",  # Starts with a digit
        "-worker",  # Starts with a dash
        "_worker",  # Starts with an underscore
        "worker!",  # Contains an invalid character
        " worker",  # Starts with a space
        "worker ",  # Ends with a space
        "a" * 31,  # Exceeds maximum length
    ]

    for name in invalid_names:
        with pytest.raises(InvalidDynoNameError):
            Worker(name, lambda: 1.23)


def test_missing_dyno_proc_error():
    with pytest.raises(MissingDynoProcError):
        Worker("worker")
