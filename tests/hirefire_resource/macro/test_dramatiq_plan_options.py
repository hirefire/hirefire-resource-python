import os

import dramatiq
import pytest
from dramatiq import Message
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker

from hirefire_resource import HireFire
from hirefire_resource.macro import dramatiq as dramatiq_macro
from tests.helpers import sample_plan

REDIS_URL = f"redis://127.0.0.1:{os.environ.get('REDIS_PORT', '6379')}/0"


def test_plan_connection_options_empty_without_env(monkeypatch):
    monkeypatch.delenv("HIREFIRE_DRAMATIQ_URL", raising=False)
    monkeypatch.delenv("HIREFIRE_DRAMATIQ_NAMESPACE", raising=False)
    assert dramatiq_macro.plan_connection_options() == {}


def test_plan_connection_options_prefer_hirefire_dramatiq_url(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "redis://hf/0")
    assert dramatiq_macro.plan_connection_options() == {"broker_url": "redis://hf/0"}


def test_plan_connection_options_namespace(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "appns")
    assert dramatiq_macro.plan_connection_options() == {"namespace": "appns"}


def test_plan_connection_options_url_and_namespace(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "amqp://hf")
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "appns")
    assert dramatiq_macro.plan_connection_options() == {
        "broker_url": "amqp://hf",
        "namespace": "appns",
    }


def test_plan_connection_options_ignore_blank(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "   ")
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "  ")
    assert dramatiq_macro.plan_connection_options() == {}


def test_plan_connection_options_blank_url_keeps_namespace(monkeypatch):
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "")
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "kept")
    assert dramatiq_macro.plan_connection_options() == {"namespace": "kept"}


def test_plan_options_empty_for_v1():
    assert dramatiq_macro.plan_options("jqs", {"skip_working": True}) == {}
    assert dramatiq_macro.plan_options("jql", None) == {}


def test_supports_plan_strategy():
    assert dramatiq_macro.supports_plan_strategy("jql")
    assert dramatiq_macro.supports_plan_strategy("jqs")
    assert not dramatiq_macro.supports_plan_strategy("cpu")
    assert not dramatiq_macro.supports_plan_strategy("rpm")
    assert not dramatiq_macro.supports_plan_strategy("jql ")
    assert not dramatiq_macro.supports_plan_strategy(None)


def test_sample_wave_hooks_open_and_close_dq_memo():
    from hirefire_resource.plan import hooks

    assert dramatiq_macro.before_sample_job_queues is not hooks.before_sample_job_queues
    token = dramatiq_macro.before_sample_job_queues()
    assert token is True
    dramatiq_macro.after_sample_job_queues(token)
    dramatiq_macro.reinit_after_fork()


def test_the_macro_takes_the_strategies_and_options_the_server_sends():
    assert dramatiq_macro.supports_plan_strategy("jqs") is True
    assert dramatiq_macro.supports_plan_strategy("jql") is True
    assert dramatiq_macro.queues_required() is True
    assert hasattr(dramatiq_macro, "job_queue_working") is False
    assert dramatiq_macro.plan_options("jqs", {"skip_working": True}) == {}
    assert dramatiq_macro.plan_options("jqs", {}) == {}
    assert dramatiq_macro.plan_options("jql", {"skip_working": True}) == {}


@pytest.fixture
def app_broker(monkeypatch):
    def use(broker):
        monkeypatch.setattr(dramatiq.broker, "global_broker", broker)
        return broker

    monkeypatch.delenv("HIREFIRE_DRAMATIQ_URL", raising=False)
    monkeypatch.delenv("HIREFIRE_DRAMATIQ_NAMESPACE", raising=False)
    return use


def test_a_plan_sample_follows_the_broker_of_the_app(app_broker):
    class AppBroker(RedisBroker):
        pass

    for broker in (RedisBroker(url=REDIS_URL), AppBroker(url=REDIS_URL)):
        app_broker(broker)

        assert dramatiq_macro.plan_connection_options() == {"broker": broker}
        assert dramatiq_macro.plan_source(broker=broker) == (
            f"the {type(broker).__name__} of the app"
        )


def test_a_plan_sample_reads_the_url_of_the_plan_before_the_broker_of_the_app(
    app_broker, monkeypatch
):
    app_broker(RedisBroker(url=REDIS_URL))
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_URL", "redis://user:secret@hf/0")

    assert dramatiq_macro.plan_connection_options() == {
        "broker_url": "redis://user:secret@hf/0"
    }
    assert dramatiq_macro.plan_source(broker_url="redis://user:secret@hf/0") == (
        "redis://***@hf/0"
    )


def test_a_plan_sample_keeps_a_namespace_from_the_environment_beside_the_app_broker(
    app_broker, monkeypatch
):
    broker = app_broker(RedisBroker(url=REDIS_URL))
    monkeypatch.setenv("HIREFIRE_DRAMATIQ_NAMESPACE", "appns")

    assert dramatiq_macro.plan_connection_options() == {
        "broker": broker,
        "namespace": "appns",
    }


@pytest.mark.parametrize("broker", [None, StubBroker()])
def test_a_plan_sample_reads_the_environment_without_a_broker_it_can_read(
    app_broker, monkeypatch, broker
):
    app_broker(broker)
    monkeypatch.setenv("REDIS_URL", "redis://user:secret@env-host:6379/2")
    for key in ("AMQP_URL", "RABBITMQ_URL", "RABBITMQ_BIGWIG_URL", "CLOUDAMQP_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("REDIS_TLS_URL", raising=False)

    assert dramatiq_macro.plan_connection_options() == {}
    assert dramatiq_macro.plan_source() == "redis://***@env-host:6379/2"


def test_a_plan_sample_counts_the_queue_of_the_app_broker(app_broker, monkeypatch):
    broker = app_broker(RedisBroker(url=REDIS_URL, namespace="appns"))
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    try:
        broker.client.delete("appns:orders", "appns:orders.msgs")
        broker.declare_queue("orders")
        broker.enqueue(
            Message(
                queue_name="orders", actor_name="work", args=(), kwargs={}, options={}
            )
        )
        HireFire.configuration.buffer.flush()

        sample_plan(
            {
                "name": "worker",
                "adapter": "dramatiq",
                "strategy": "jqs",
                "queues": ["orders"],
            }
        )

        assert list(
            HireFire.configuration.buffer.flush()["worker"]["jqs"].values()
        ) == [1]
    finally:
        broker.client.delete("appns:orders", "appns:orders.msgs")
        broker.close()
