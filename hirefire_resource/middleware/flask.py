import json

from flask import Response, request

from autoscale_agent.agent import Agent
from autoscale_agent.middleware import Middleware as BaseMiddleware
from autoscale_agent.middleware import RequestInfo


class Middleware:
    def __init__(self, app):
        self.app = app
        self.original_wsgi_app = app.wsgi_app

    def __call__(self, environ, start_response):
        with self.app.request_context(environ):
            middleware = BaseMiddleware(Agent.configuration)
            request_info = RequestInfo(request.path, request.environ)
            response = middleware.process_request(request_info)

            if isinstance(response, tuple):
                status, headers, body = response

                if status == 200:
                    response = Response(
                        json.loads(body), content_type="application/json"
                    )
                else:
                    response = Response(body, status=404)

                for key, value in headers.items():
                    response.headers[key] = value

            if response:
                return response(environ, start_response)

        return self.original_wsgi_app(environ, start_response)
