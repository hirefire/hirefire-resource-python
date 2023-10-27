from flask import Response, request

from hirefire_resource import Resource
from hirefire_resource.middleware import Middleware as BaseMiddleware
from hirefire_resource.middleware import RequestInfo


class Middleware:
    """
    Flask middleware for processing requests related to HireFire.

    Acts as a bridge between Flask's request/response flow and the
    base HireFire middleware. It extracts request information from Flask's request object
    and forwards it to the base middleware. If the request is relevant to HireFire,
    it constructs a response accordingly. Otherwise, it continues the regular request flow.

    Attributes:
        app (Flask): The Flask application instance.
        original_wsgi_app (WSGI application): The original WSGI application function that this middleware wraps.
    """

    def __init__(self, app):
        """
        Initialize the middleware with a given Flask application instance.

        Args:
            app (Flask): The Flask application instance.
        """
        self.app = app
        self.original_wsgi_app = app.wsgi_app

    def __call__(self, environ, start_response):
        """
        Process the incoming WSGI request.

        It extracts the request information, forwards it to the base HireFire middleware,
        and either returns a HireFire related response or proceeds with the regular
        request processing chain.

        Args:
            environ (dict): The WSGI environment dictionary.
            start_response (function): The WSGI start_response function.

        Returns:
            Response: The response to be returned to the client in WSGI format.
        """
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
