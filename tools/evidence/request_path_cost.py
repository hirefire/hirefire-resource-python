import asyncio
import json
import os
import sys
import time

from evidence_support import boot

N = 100_000
HEADERS = [(f"x-header-{index}".encode(), b"value") for index in range(20)]


def best(function, repeat=5):
    timings = []
    for _ in range(repeat):
        started = time.perf_counter_ns()
        function()
        timings.append((time.perf_counter_ns() - started) / N)
    return round(min(timings))


def main():
    mode = sys.argv[1]
    with_library = mode == "with_job_library"
    if with_library:
        import rq  # noqa: F401
    token = None if mode == "no_token" else "evidence-token"
    hirefire, server, lines = boot(env={"DYNO": "web.1"}, token=token)
    from hirefire_resource.middleware.asgi.starlette import HireFireMiddleware as Asgi
    from hirefire_resource.middleware.wsgi.flask import HireFireMiddleware as Wsgi

    def app(environ, start_response):
        return ()

    wsgi = Wsgi(app)

    async def asgi_app(scope, receive, send):
        return None

    asgi = Asgi(asgi_app)

    def wsgi_loop(target, header):
        def run():
            now = time.time
            for _ in range(N):
                environ = {"REQUEST_METHOD": "GET"}
                if header:
                    environ["HTTP_X_REQUEST_START"] = f"t={now() - 0.02}"
                target(environ, None)

        return run

    def asgi_loop(target, header):
        async def run():
            now = time.time
            for _ in range(N):
                headers = list(HEADERS)
                if header:
                    headers.append((b"x-request-start", f"t={now() - 0.02}".encode()))
                await target({"type": "http", "headers": headers}, None, None)

        return lambda: asyncio.run(run())

    result = {
        "mode": mode,
        "wsgi_bare_ns": best(wsgi_loop(app, True)),
        "wsgi_middleware_with_header_ns": best(wsgi_loop(wsgi, True)),
        "wsgi_middleware_without_header_ns": best(wsgi_loop(wsgi, False)),
        "asgi_bare_ns": best(asgi_loop(asgi_app, True)),
        "asgi_middleware_with_header_ns": best(asgi_loop(asgi, True)),
        "asgi_middleware_without_header_ns": best(asgi_loop(asgi, False)),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
    }
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
