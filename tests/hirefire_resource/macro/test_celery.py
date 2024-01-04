import datetime
import math
import time

import pytest
from celery import Celery

from hirefire_resource.macro.celery import job_queue_latency, job_queue_size

broker_urls = ["redis://localhost:6379/12", "amqp://guest:guest@localhost:5672/"]


@pytest.fixture(scope="session", params=broker_urls)
def celery_app(request):
    return Celery(broker=request.param)


@pytest.fixture(autouse=True)
def clear_broker(celery_app):
    broker_url = celery_app.conf.broker_url

    if broker_url.startswith("redis://"):
        with celery_app.connection_or_acquire() as connection:
            connection.default_channel.client.flushdb()

    elif broker_url.startswith("amqp://"):
        with celery_app.connection_or_acquire() as connection:
            channel = connection.default_channel
            for queue in ["default", "mailer"]:
                channel.queue_delete(queue=queue)
                channel.queue_declare(queue=queue, durable=True, auto_delete=False)


def test_job_queue_size_without_jobs(celery_app):
    assert job_queue_size("default", broker_url=celery_app.conf.broker_url) == 0


def test_job_queue_size_with_jobs(celery_app):
    for _ in range(5):
        celery_app.send_task("test_task", queue="default")
        celery_app.send_task("test_task", queue="mailer")

    assert job_queue_size("default", broker_url=celery_app.conf.broker_url) == 5
    assert (
        job_queue_size("default", "mailer", broker_url=celery_app.conf.broker_url) == 10
    )


def test_job_queue_latency_without_jobs(celery_app):
    assert job_queue_latency("default", broker_url=celery_app.conf.broker_url) == 0


def enqueue_for_job_queue_latency_with_job(celery_app):
    now = time.time()

    for i in reversed(range(5)):
        celery_app.send_task("test_task", queue="default", headers={"run_at": now - i})
        celery_app.send_task(
            "test_task", queue="mailer", headers={"run_at": now - i * 2}
        )


def test_job_queue_latency_with_jobs(celery_app):
    enqueue_for_job_queue_latency_with_job(celery_app)

    assert math.isclose(
        job_queue_latency("default", broker_url=celery_app.conf.broker_url),
        4,
        abs_tol=1,
    )
    assert math.isclose(
        job_queue_latency("mailer", broker_url=celery_app.conf.broker_url), 8, abs_tol=1
    )

    # Verify that peeking doesn't discard the message
    assert (
        job_queue_size("default", "mailer", broker_url=celery_app.conf.broker_url) == 10
    )


def test_job_queue_latency_with_jobs_multi(celery_app):
    enqueue_for_job_queue_latency_with_job(celery_app)

    assert math.isclose(
        job_queue_latency("default", "mailer", broker_url=celery_app.conf.broker_url),
        8,
        abs_tol=1,
    )

    # Verify that peeking doesn't discard the message
    assert (
        job_queue_size("default", "mailer", broker_url=celery_app.conf.broker_url) == 10
    )


# Does reject also update the run_at header?


def enqueue_for_job_queue_latency_with_eta(celery_app):
    now = time.time()

    for i in reversed(range(5)):
        celery_app.send_task(
            "test_task", queue="default", eta=datetime.datetime.fromtimestamp(now - i)
        )
        celery_app.send_task(
            "test_task",
            queue="mailer",
            eta=datetime.datetime.fromtimestamp(now - i * 2),
        )


def test_job_queue_latency_with_eta(celery_app):
    enqueue_for_job_queue_latency_with_eta(celery_app)

    assert math.isclose(
        job_queue_latency("default", broker_url=celery_app.conf.broker_url),
        4,
        abs_tol=1,
    )
    assert math.isclose(
        job_queue_latency("mailer", broker_url=celery_app.conf.broker_url), 8, abs_tol=1
    )

    # Verify that peeking doesn't discard the message
    assert (
        job_queue_size("default", "mailer", broker_url=celery_app.conf.broker_url) == 10
    )


def enqueue_for_job_queue_latency_with_countdown(celery_app):
    for i in reversed(range(5)):
        celery_app.send_task("test_task", queue="default", countdown=-i)
        celery_app.send_task("test_task", queue="mailer", countdown=-i * 2)


def test_job_queue_latency_with_countdown(celery_app):
    enqueue_for_job_queue_latency_with_countdown(celery_app)

    assert math.isclose(
        job_queue_latency("default", broker_url=celery_app.conf.broker_url),
        4,
        abs_tol=1,
    )
    assert math.isclose(
        job_queue_latency("mailer", broker_url=celery_app.conf.broker_url), 8, abs_tol=1
    )

    # Verify that peeking doesn't discard the message
    assert (
        job_queue_size("default", "mailer", broker_url=celery_app.conf.broker_url) == 10
    )
