from django.http import HttpResponse

from hirefire_resource.middleware import Middleware as BaseMiddleware
from hirefire_resource.middleware import RequestInfo
from hirefire_resource.resource import Resource


class Middleware:
    """
    Django middleware for processing requests related to HireFire.

    Acts as a bridge between Django's request/response flow and the
    base HireFire middleware. It extracts request information from Django's request object
    and forwards it to the base middleware. If the request is relevant to HireFire,
    it constructs a response accordingly. Otherwise, it continues the regular request flow.

    Attributes:
        get_response (function): A reference to the next middleware or view in the processing chain.
    """

    def __init__(self, get_response):
        """
        Initialize the middleware with a given get_response function.

        Args:
            get_response (function): A reference to the next middleware or view in the processing chain.
        """
        self.get_response = get_response

    def __call__(self, request):
        """
        Process the incoming Django request.

        It extracts the request information, forwards it to the base HireFire middleware,
        and either returns a HireFire related response or proceeds with the regular
        request processing chain.

        Args:
            request (HttpRequest): The incoming Django request object.

        Returns:
            HttpResponse: The response to be returned to the client.
        """
        base_middleware = BaseMiddleware(Resource.configuration)
        request_info = RequestInfo(request.path, request.META)
        response_data = base_middleware.process_request(request_info)

        if isinstance(response_data, tuple):
            status, headers, body = response_data
            response = HttpResponse(content=body, status=status)

            for key, value in headers.items():
                response[key] = value

            return response
        else:
            return self.get_response(request)
