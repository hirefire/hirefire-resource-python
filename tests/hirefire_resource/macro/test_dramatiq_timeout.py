import socket
import threading
import time
from types import SimpleNamespace

import pytest

pika = pytest.importorskip("pika")

from hirefire_resource.macro import dramatiq as dramatiq_macro
from hirefire_resource.macro.dramatiq import (
    _SAMPLE_AMQP_TIMEOUT,
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
    assert raised is None
    assert result in (0, 0.0)


def test_sample_pika_parameters_set_lease_safe_timeouts():
    parameters = dramatiq_macro._sample_pika_parameters(
        url="amqp://guest:guest@127.0.0.1:5672/"
    )
    assert parameters.socket_timeout == _SAMPLE_AMQP_TIMEOUT
    assert parameters.stack_timeout == _SAMPLE_AMQP_TIMEOUT
    assert parameters.blocked_connection_timeout == _SAMPLE_AMQP_TIMEOUT
    assert parameters.connection_attempts == 1
    assert parameters.retry_delay == 0


def test_injected_broker_parameters_are_copied_not_mutated(monkeypatch):
    original = pika.URLParameters("amqp://guest:guest@127.0.0.1:1/")
    original.socket_timeout = 99
    original.stack_timeout = 99
    original.blocked_connection_timeout = 99
    original.connection_attempts = 4
    original.retry_delay = 2
    captured: dict[str, object] = {}

    broker = SimpleNamespace(parameters=original)
    monkeypatch.setattr(dramatiq_macro, "_is_rabbitmq_broker", lambda _broker: True)

    def fake_blocking(parameters=None):
        captured["socket_timeout"] = parameters.socket_timeout
        captured["stack_timeout"] = parameters.stack_timeout
        captured["blocked_connection_timeout"] = parameters.blocked_connection_timeout
        captured["connection_attempts"] = parameters.connection_attempts
        captured["retry_delay"] = parameters.retry_delay
        raise OSError("stop after params")

    monkeypatch.setattr(pika, "BlockingConnection", fake_blocking)

    assert job_queue_size("default", broker=broker) == 0
    assert original.socket_timeout == 99
    assert original.stack_timeout == 99
    assert original.blocked_connection_timeout == 99
    assert original.connection_attempts == 4
    assert original.retry_delay == 2
    assert captured["socket_timeout"] == _SAMPLE_AMQP_TIMEOUT
    assert captured["stack_timeout"] == _SAMPLE_AMQP_TIMEOUT
    assert captured["blocked_connection_timeout"] == _SAMPLE_AMQP_TIMEOUT
    assert captured["connection_attempts"] == 1
    assert captured["retry_delay"] == 0


def test_sample_pika_parameters_copies_each_host_in_a_list():
    first = pika.URLParameters("amqp://guest:guest@127.0.0.1:5672/")
    second = pika.URLParameters("amqp://guest:guest@127.0.0.1:5673/")
    first.socket_timeout = 99
    second.socket_timeout = 88
    source = [first, second]

    copied = dramatiq_macro._sample_pika_parameters(source=source)

    assert isinstance(copied, list)
    assert len(copied) == 2
    assert copied[0] is not first
    assert copied[1] is not second
    assert copied[0].socket_timeout == _SAMPLE_AMQP_TIMEOUT
    assert copied[1].socket_timeout == _SAMPLE_AMQP_TIMEOUT
    assert copied[0].stack_timeout == _SAMPLE_AMQP_TIMEOUT
    assert copied[1].blocked_connection_timeout == _SAMPLE_AMQP_TIMEOUT
    assert copied[0].connection_attempts == 1
    assert copied[1].retry_delay == 0
    assert first.socket_timeout == 99
    assert second.socket_timeout == 88


def test_owned_amqp_size_times_out_on_blackhole():
    server, conns, stop, port = _blackhole_port()
    try:
        url = f"amqp://guest:guest@127.0.0.1:{port}//"
        _assert_sample_bounded(lambda: job_queue_size("default", broker_url=url))
    finally:
        _stop_blackhole(server, conns, stop)


def test_owned_amqp_latency_times_out_on_blackhole():
    server, conns, stop, port = _blackhole_port()
    try:
        url = f"amqp://guest:guest@127.0.0.1:{port}//"
        _assert_sample_bounded(lambda: job_queue_latency("default", broker_url=url))
    finally:
        _stop_blackhole(server, conns, stop)
