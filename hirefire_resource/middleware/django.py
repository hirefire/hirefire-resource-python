from django.http import HttpResponse

from hirefire_resource.middleware import Middleware as BaseMiddleware
from hirefire_resource.middleware import RequestInfo
from hirefire_resource.resource import Resource


class Middleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
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
