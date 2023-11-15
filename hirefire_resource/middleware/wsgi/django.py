from django.http import HttpResponse

from hirefire_resource.middleware.wsgi import RequestInfo, request


class Middleware:
    """
    Django (WSGI) middleware for autoscaling Heroku web and worker dynos using HireFire.

    This middleware interacts with the 'request' function to determine how to process each incoming
    HTTP request. Based on the output of the 'request' function, it either responds with job queue
    metrics for autoscaling purposes or passes the request to the next middleware in the stack.

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
