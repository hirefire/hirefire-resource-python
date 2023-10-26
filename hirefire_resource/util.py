import http.client
import os
import time


def debug(*args, **kwargs):
    if os.getenv("AUTOSCALE_DEBUG"):
        print(*args, **kwargs)


def dispatch(body, token):
    headers = {
        "User-Agent": "Autoscale Agent (Python)",
        "Autoscale-Metric-Token": token,
        "Content-Type": "application/json",
    }
    body_bytes = body.encode("utf-8")
    conn = http.client.HTTPSConnection("metrics.autoscale.app", timeout=5)
    conn.request("POST", "/", body=body_bytes, headers=headers)
    response = conn.getresponse()
    conn.close()
    return response


def loop_with_interval(interval, calleable):
    while True:
        start = time.time()
        calleable()
        end = time.time()
        elapsed = end - start
        if elapsed < interval:
            time.sleep(interval - elapsed)
