from flask import Response

from hirefire_resource.middleware.wsgi import RequestInfo, request


class HireFireMiddleware:
    def __init__(self, app):
        self.app = app
        self.original_wsgi_app = app.wsgi_app

    def __call__(self, environ, start_response):
        with self.app.request_context(environ):
            response = request(
                RequestInfo(
                    path=environ.get("PATH_INFO"),
                    request_start_time=environ.get("HTTP_X_REQUEST_START"),
                    token=environ.get("HTTP_HIREFIRE_TOKEN"),
                )
            )

            if response:
                status, headers, body = response
                response = Response(body, status=status, headers=headers)
                return response(environ, start_response)

        return self.original_wsgi_app(environ, start_response)
