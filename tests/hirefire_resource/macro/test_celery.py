import pytest
from celery import Celery

from hirefire_resource.macro.celery import job_queue_size

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
