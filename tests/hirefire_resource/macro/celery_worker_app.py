import os
import threading
import time

from celery import Celery

app = Celery("hirefire-test-worker", broker=os.environ["HIREFIRE_TEST_BROKER_URL"])
app.conf.worker_prefetch_multiplier = 4

_lifetime = threading.Timer(120, os._exit, [1])
_lifetime.daemon = True
_lifetime.start()


@app.task(name="hirefire.test.hold")
def hold():
    time.sleep(120)
