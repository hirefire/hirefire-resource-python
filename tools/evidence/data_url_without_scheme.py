import json
import os
import time

from evidence_support import boot


def run(url):
    os.environ["HIREFIRE_DATA_URL"] = url
    hirefire, server, lines = boot(env={"DYNO": "web.1", "HIREFIRE_DATA_URL": url})
    time.sleep(3.2)
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    return {
        "url": url,
        "error_lines_in_3s": len(lines),
        "example": lines[1] if len(lines) > 1 else lines[:1],
    }


if __name__ == "__main__":
    import sys

    print(json.dumps(run(sys.argv[1]), indent=2))
