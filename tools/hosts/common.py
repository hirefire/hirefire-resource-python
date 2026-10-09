import logging
import os
import sys

from hirefire_resource import HireFire


def boot():
    logger = logging.getLogger("hosts")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("HF pid=%(process)d %(message)s"))
        logger.addHandler(handler)
    with HireFire.configure() as config:
        config.logger = logger
    print(f"BOOT pid={os.getpid()} ppid={os.getppid()}", file=sys.stderr, flush=True)
