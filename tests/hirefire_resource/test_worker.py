from hirefire_resource.worker import InvalidDynoName, MissingDynoProc, Worker


def test_worker_initialization_and_methods():
    worker = Worker("worker", lambda: 1.23)
    assert worker.name == "worker"
    assert worker.call() == 1.23


def test_invalid_dyno_name_exception():
    with pytest.raises(InvalidDynoName):
        Worker("invalid name!", lambda: 1.23)


def test_missing_dyno_proc_exception():
    with pytest.raises(MissingDynoProc):
        Worker("worker")
