from django.http import HttpResponse

from hirefire_resource.middleware.wsgi import RequestInfo, request


class Middleware:
    """
    Django (WSGI) middleware for autoscaling Heroku web and worker dynos using HireFire.

    This middleware delegates request processing to the `request` function.  It handles incoming
    HTTP requests by analyzing the request path and start time.

    The middleware checks for specific conditions (like request path) and, if met, responds with the
    necessary job queue metrics. If the conditions are not met, it passes control to the next
    middleware in the stack.

    Attributes:
        get_response (callable): The next middleware or view in Django's request-response processing chain.
    """

    def __init__(self, get_response):
        """
        Initialize the middleware with the get_response callable needed for Django's middleware pattern.

        Args:
            get_response (callable): The next middleware or view in Django's request-response processing chain.
        """
        self.get_response = get_response

    def __call__(self, req):
        """
        Synchronous call method to process all incoming WSGI requests.

        Args:
            request (HttpRequest): The incoming Django request object.

        Returns:
            HttpResponse or callable: A Django HttpResponse if the request is for the HireFire info path,
                                      otherwise the result of the get_response callable for further processing.
        """
        response = request(
            RequestInfo(
                path=req.path,
                request_start_time=req.META.get("HTTP_X_REQUEST_START", ""),
            )
        )

        if response:
            status, headers, body = response
            response = HttpResponse(content=body, status=status, headers=headers)
            return response

        return self.get_response(request)
