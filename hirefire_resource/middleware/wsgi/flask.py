from flask import Response

from hirefire_resource.middleware.wsgi import RequestInfo, request


class Middleware:
    """
    Flask (WSGI) middleware for autoscaling Heroku web and worker dynos using HireFire.

    This middleware delegates request processing to the `request` function.  It handles incoming
    HTTP requests by analyzing the request path and start time.

    The middleware checks for specific conditions (like request path) and, if met, responds with the
    necessary job queue metrics. If the conditions are not met, it passes control to the next
    middleware in the stack.

    Attributes:
        app (Flask): The Flask application instance.
        original_wsgi_app (callable): The original WSGI application callable that this middleware wraps.
    """

    def __init__(self, app):
        """
        Initializes the middleware with the next WSGI application or middleware in the stack.

        Args:
            app (WSGI application): The WSGI application instance. This could be a Flask
            application, another WSGI-compatible application, or a middleware component.
        """
        self.app = app
        self.original_wsgi_app = app.wsgi_app

    def __call__(self, environ, start_response):
        """
        Synchronous call method to process all incoming WSGI requests.

        Args:
            environ (dict): The WSGI environment dict containing request data.
            start_response (callable): The WSGI start_response callable used to initiate the HTTP response.

        Returns:
            Response or iterable: A Flask Response object if the request is for the HireFire info path, or
                                  the result of the original WSGI application callable.
        """
        with self.app.request_context(environ):
            response = request(
                RequestInfo(
                    path=environ.get("PATH_INFO"),
                    request_start_time=environ.get("HTTP_X_REQUEST_START", ""),
                )
            )

            if response:
                status, headers, body = response
                response = Response(body, status=status, headers=headers)
                return response(environ, start_response)

        return self.original_wsgi_app(environ, start_response)
