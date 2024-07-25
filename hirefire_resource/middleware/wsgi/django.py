from django.http import HttpResponse

from hirefire_resource.middleware.wsgi import RequestInfo, request


class HireFireMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, req):
        response_data = request(
            RequestInfo(
                path=req.path,
                request_start_time=req.META.get("HTTP_X_REQUEST_START"),
                token=req.META.get("HTTP_HIREFIRE_TOKEN"),
            )
        )

        if response_data:
            status, headers, body = response_data
            response = HttpResponse(content=body, status=status)
            for key, value in headers.items():
                response[key] = value
            return response

        return self.get_response(req)
