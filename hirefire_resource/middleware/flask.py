from flask import Response, request

from hirefire_resource import Resource
from hirefire_resource.middleware import Middleware as BaseMiddleware
from hirefire_resource.middleware import RequestInfo


class Middleware:
    def __init__(self, app):
        self.app = app
        self.original_wsgi_app = app.wsgi_app

    def __call__(self, environ, start_response):
        with self.app.request_context(environ):
            middleware = BaseMiddleware(Resource.configuration)
            request_info = RequestInfo(request.path, request.environ)
            response_data = middleware.process_request(request_info)

            if isinstance(response_data, tuple):
                status, headers, body = response_data
                response = Response(body, status=status)

                for key, value in headers.items():
                    response.headers[key] = value

                return response(environ, start_response)

        return self.original_wsgi_app(environ, start_response)
