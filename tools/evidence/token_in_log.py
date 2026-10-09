import json
import time

from evidence_support import boot

SECRET = "s3cr3t-token\nX-Injected: 1"


def main():
    hirefire, server, lines = boot(env={"DYNO": "web.1"}, token=SECRET)
    time.sleep(2.5)
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    leaked = [line for line in lines if "s3cr3t-token" in line]
    print(
        json.dumps(
            {
                "requests_received": len(server.requests),
                "log_lines": len(lines),
                "lines_with_token": len(leaked),
                "example": leaked[0] if leaked else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
