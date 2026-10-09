import os
import time

from celery import Celery

app = Celery(
    "tasks", broker=os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
)
app.conf.worker_hijack_root_logger = False

from common import boot  # noqa: E402

boot()


@app.task
def work(seconds=0.0):
    time.sleep(seconds)
    return os.getpid()
