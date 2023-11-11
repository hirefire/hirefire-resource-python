from django.http import HttpResponse

from hirefire_resource.middleware.wsgi import RequestInfo, request


class Middleware:
    """
    Django-specific middleware for processing requests related to HireFire.

    This middleware standardizes Django's request object and response flow to be compatible with
    the HireFire base middleware. It captures and processes request queue time data, and if the
    request is for the HireFire info path, it responds with the appropriate data. For all other
    requests, it passes the request through the normal Django request handling process.

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
        Process the incoming Django request by standardizing it and passing it to the base HireFire middleware.
        If the request is for the HireFire info path, an appropriate response is returned. Otherwise, the request
        is passed on to the next middleware or view.

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
