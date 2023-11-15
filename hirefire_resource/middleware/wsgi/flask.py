from flask import Response

from hirefire_resource.middleware.wsgi import RequestInfo, request


class Middleware:
    """
    Flask (WSGI) middleware for autoscaling Heroku web and worker dynos using HireFire.

    This middleware interacts with the 'request' function to determine how to process each incoming
    HTTP request. Based on the output of the 'request' function, it either responds with job queue
    metrics for autoscaling purposes or passes the request to the next middleware in the stack.

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

        Within a Flask request context, this method evaluates the request using the 'request'
        function from hirefire_resource.middleware.wsgi. If the 'request' function indicates a
        custom response is required (e.g., job queue metrics for autoscaling), a Flask Response
        object is created and returned.  Otherwise, the request is passed to the original WSGI
        application callable for further processing.

        Args:
            environ (dict): The WSGI environment dict containing request data.
            start_response (callable): The WSGI start_response callable used to initiate the HTTP
                                       response.

        Returns:
            Response or iterable: A Flask Response object or the result of the original WSGI
                                  application callable.
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
