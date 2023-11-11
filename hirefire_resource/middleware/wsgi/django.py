from django.http import HttpResponse

from hirefire_resource import HireFire
from hirefire_resource.middleware import RequestInfo
from hirefire_resource.middleware.wsgi import BaseMiddleware


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

    def __call__(self, request):
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
        base_middleware = BaseMiddleware(HireFire.configuration)
        request_info = RequestInfo(
            path=request.path,
            request_start_time=request.META.get("HTTP_X_REQUEST_START", ""),
        )
        response_data = base_middleware.process_request(request_info)

        if response_data:
            status, headers, body = response_data
            response = HttpResponse(content=body, status=status, headers=headers)
            return response

        return self.get_response(request)
