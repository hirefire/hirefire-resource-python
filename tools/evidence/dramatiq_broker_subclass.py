import json
import os

from dramatiq.brokers.redis import RedisBroker

from hirefire_resource.macro.dramatiq import job_queue_size

BROKER = os.environ["TOOLS_REDIS_URL"]


class AppBroker(RedisBroker):
    pass


def attempt(broker):
    try:
        return {"size": job_queue_size("default", broker=broker)}
    except Exception as error:
        return {"raised": f"{type(error).__name__}: {error}"}


def main():
    print(
        json.dumps(
            {
                "RedisBroker": attempt(RedisBroker(url=BROKER)),
                "subclass_of_RedisBroker": attempt(AppBroker(url=BROKER)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
