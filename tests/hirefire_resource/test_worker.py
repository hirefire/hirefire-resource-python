from hirefire_resource.worker import Worker

def test_attributes_and_methods():
    worker = Worker("worker", lambda: 1.23)
    assert "worker" == worker.name
    assert 1.23 == worker.proc()
