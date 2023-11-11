from flask import Response, request

from hirefire_resource.middleware import RequestInfo
from hirefire_resource.middleware.wsgi import BaseMiddleware
from hirefire_resource import HireFire


class Middleware:
    """
    Flask middleware for processing requests related to HireFire.

    This middleware serves as an intermediary between Flask's request/response flow and the
    HireFire base middleware. It extracts request information from Flask's global request object,
    standardizes it, and forwards it to the base middleware. If the request is for the HireFire
    info path, this middleware constructs and returns a response with the appropriate metrics.
    For all other requests, it continues the normal Flask request handling process.

    To properly capture request queue times, this middleware should be placed early in the Flask
    middleware stack, ideally as one of the first middlewares to be executed.

    Attributes:
        app (Flask): The Flask application instance.
        original_wsgi_app (callable): The original WSGI application callable that this middleware wraps.
    """

    def __init__(self, app):
        """
        Initializes the middleware with the given Flask application instance.

        The middleware wraps the Flask application's WSGI interface, intercepting requests to
        process them for HireFire metrics or to allow normal request processing.

        Args:
            app (Flask): The Flask application instance.
        """
        self.app = app
        self.original_wsgi_app = app.wsgi_app

    def __call__(self, environ, start_response):
        """
        Processes the incoming WSGI request, forwarding it to the base HireFire middleware.

        Extracts the necessary request information and determines if the request is for the
        HireFire info path. If it is, the middleware constructs and returns a response with the
        HireFire metrics. Otherwise, the request is passed to the original Flask application
        for regular processing.

        Args:
            environ (dict): The WSGI environment dict containing request data.
            start_response (callable): The WSGI start_response callable used to initiate the HTTP response.

        Returns:
            Response or iterable: A Flask Response object if the request is for the HireFire info path, or
                                  the result of the original WSGI application callable.
        """
        with self.app.request_context(environ):
            base_middleware = BaseMiddleware(HireFire.configuration)
            request_info = RequestInfo(
                path=request.path,
                request_start_time=request.environ.get("HTTP_X_REQUEST_START", ""),
            )
            response_data = base_middleware.process_request(request_info)

            if response_data:
                status, headers, body = response_data
                response = Response(body, status=status, headers=headers)
                return response(environ, start_response)

        return self.original_wsgi_app(environ, start_response)
