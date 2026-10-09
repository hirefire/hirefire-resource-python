import json
import os
import sys

URL = os.environ["TOOLS_REDIS_TLS_URL"]


def attempt(call):
    try:
        return {"value": call()}
    except Exception as error:
        return {"raised": f"{type(error).__name__}: {str(error)[:150]}"}


def main():
    adapter = sys.argv[1]
    relaxed = URL + "?ssl_cert_reqs=none"
    if adapter == "rq":
        from hirefire_resource.macro.rq import job_queue_size

        result = {
            "url_as_given": attempt(lambda: job_queue_size("default", redis_url=URL)),
            "url_with_ssl_cert_reqs_none": attempt(
                lambda: job_queue_size("default", redis_url=relaxed)
            ),
        }
    elif adapter == "celery":
        from hirefire_resource.macro.celery import job_queue_size

        result = {
            "url_as_given": attempt(
                lambda: job_queue_size("default", broker_url=URL, skip_working=True)
            ),
            "url_with_ssl_cert_reqs_none": attempt(
                lambda: job_queue_size("default", broker_url=relaxed, skip_working=True)
            ),
        }
    else:
        from hirefire_resource.macro.dramatiq import job_queue_size

        result = {
            "url_as_given": attempt(lambda: job_queue_size("default", broker_url=URL)),
            "url_with_ssl_cert_reqs_none": attempt(
                lambda: job_queue_size("default", broker_url=relaxed)
            ),
        }
    print(json.dumps({"adapter": adapter, **result}))


if __name__ == "__main__":
    main()
