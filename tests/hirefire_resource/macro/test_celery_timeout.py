import socket
import threading
import time

from celery import Celery

from hirefire_resource.macro.celery import (
    _SAMPLE_BROKER_TIMEOUT,
    _owned_celery_app,
    job_queue_latency,
    job_queue_size,
)


def _blackhole_port():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    port = server.getsockname()[1]
    conns: list = []
    stop = threading.Event()

    def accept_loop():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                conn.settimeout(None)
                conns.append(conn)
            except TimeoutError:
                continue
            except OSError:
                break

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    return server, conns, stop, port


def _stop_blackhole(server, conns, stop):
    stop.set()
    for conn in conns:
        try:
            conn.close()
        except OSError:
            pass
    try:
        server.close()
    except OSError:
        pass


def _assert_sample_bounded(fn):
    started = time.monotonic()
    raised = None
    result = None
    try:
        result = fn()
    except Exception as error:
        raised = error
    elapsed = time.monotonic() - started
    assert elapsed < 8, f"sample parked for {elapsed:.1f}s ({raised!r}, {result!r})"
    assert raised is not None or result in (0, 0.0)


def test_owned_celery_app_sets_redis_timeouts():
    app = _owned_celery_app("redis://localhost:6379/0")
    opts = dict(app.conf.broker_transport_options)
    assert opts["socket_timeout"] == _SAMPLE_BROKER_TIMEOUT
    assert opts["socket_connect_timeout"] == _SAMPLE_BROKER_TIMEOUT
    assert "read_timeout" not in opts
    assert app.conf.broker_connection_timeout == _SAMPLE_BROKER_TIMEOUT
    assert app.conf.broker_connection_retry is False
    assert app.conf.broker_connection_max_retries == 0


def test_owned_celery_app_sets_amqp_timeouts():
    app = _owned_celery_app("amqp://guest:guest@localhost:5672")
    opts = dict(app.conf.broker_transport_options)
    assert opts["read_timeout"] == _SAMPLE_BROKER_TIMEOUT
    assert opts["write_timeout"] == _SAMPLE_BROKER_TIMEOUT
    assert "socket_timeout" not in opts
    assert app.conf.broker_connection_timeout == _SAMPLE_BROKER_TIMEOUT


def test_caller_celery_app_is_not_given_sample_timeouts(monkeypatch):
    from contextlib import contextmanager

    from kombu.exceptions import OperationalError

    app = Celery(broker="redis://localhost:6379/0")
    app.conf.broker_transport_options = {"socket_timeout": 99}
    app.conf.broker_connection_timeout = 99

    def boom(*_args, **_kwargs):
        raise AssertionError("owned app must not be built for a caller celery_app")

    monkeypatch.setattr("hirefire_resource.macro.celery._owned_celery_app", boom)

    @contextmanager
    def skip_connect():
        raise OperationalError("skip")
        yield None

    monkeypatch.setattr(app, "connection_or_acquire", skip_connect)
    assert job_queue_size("celery", celery_app=app) == 0
    assert app.conf.broker_transport_options == {"socket_timeout": 99}
    assert app.conf.broker_connection_timeout == 99
    assert _SAMPLE_BROKER_TIMEOUT != 99


# Blackhole never answers, so the sample always parks for the full broker
# timeout. Patch it down: same code path, no 5s stall per test.
def test_owned_redis_size_times_out_on_blackhole(monkeypatch):
    monkeypatch.setattr("hirefire_resource.macro.celery._SAMPLE_BROKER_TIMEOUT", 0.3)
    server, conns, stop, port = _blackhole_port()
    try:
        url = f"redis://127.0.0.1:{port}/0"
        _assert_sample_bounded(lambda: job_queue_size("celery", broker_url=url))
    finally:
        _stop_blackhole(server, conns, stop)


def test_owned_redis_latency_times_out_on_blackhole(monkeypatch):
    monkeypatch.setattr("hirefire_resource.macro.celery._SAMPLE_BROKER_TIMEOUT", 0.3)
    server, conns, stop, port = _blackhole_port()
    try:
        url = f"redis://127.0.0.1:{port}/0"
        _assert_sample_bounded(lambda: job_queue_latency("celery", broker_url=url))
    finally:
        _stop_blackhole(server, conns, stop)


def test_owned_amqp_size_times_out_on_blackhole(monkeypatch):
    monkeypatch.setattr("hirefire_resource.macro.celery._SAMPLE_BROKER_TIMEOUT", 0.3)
    server, conns, stop, port = _blackhole_port()
    try:
        url = f"amqp://guest:guest@127.0.0.1:{port}//"
        _assert_sample_bounded(lambda: job_queue_size("celery", broker_url=url))
    finally:
        _stop_blackhole(server, conns, stop)
