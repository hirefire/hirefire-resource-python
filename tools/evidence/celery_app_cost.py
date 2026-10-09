import gc
import json
import os
import time

from celery import Celery, current_app

host = Celery("host", broker=os.environ["TOOLS_REDIS_URL"])
host.set_default()
import hirefire_resource.macro.celery as macro

before_name = current_app.main
before_objects = len(gc.get_objects())
url = os.environ["TOOLS_REDIS_URL"]
started = time.perf_counter()
N = 200
for _ in range(N):
    macro.job_queue_size("default", broker_url=url, skip_working=True)
per_call = (time.perf_counter() - started) / N * 1000
started = time.perf_counter()
for _ in range(N):
    macro._owned_celery_app(url)
construct = (time.perf_counter() - started) / N * 1000
gc.collect()
print(
    json.dumps(
        {
            "host_current_app_before": before_name,
            "current_app_after_macro_calls_on_this_thread": current_app.main,
            "ms_per_size_call": round(per_call, 2),
            "ms_per_app_construction": round(construct, 3),
            "objects_before": before_objects,
            "objects_after_400_apps_and_gc": len(gc.get_objects()),
        }
    )
)

import tracemalloc

gc.collect()
tracemalloc.start()
first = tracemalloc.take_snapshot()
for _ in range(2000):
    macro._owned_celery_app(url)
gc.collect()
second = tracemalloc.take_snapshot()
growth = sum(stat.size_diff for stat in second.compare_to(first, "filename"))
top = [
    f"{stat.size_diff // 1024} KiB {stat.traceback[0].filename.split('site-packages/')[-1]}"
    for stat in second.compare_to(first, "filename")[:3]
]
print(
    json.dumps(
        {
            "apps_built": 2000,
            "bytes_retained_per_app_after_gc": growth // 2000,
            "top": top,
        }
    )
)
