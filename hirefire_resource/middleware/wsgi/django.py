from django.http import HttpResponse

from hirefire_resource.middleware.wsgi import RequestInfo, request


class HireFireMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, req):
        response = request(
            RequestInfo(
                path=req.path,
                request_start_time=req.META.get("HTTP_X_REQUEST_START"),
                token=req.META.get("HTTP_HIREFIRE_TOKEN"),
            )
        )

        if response:
            status, headers, body = response
            response = HttpResponse(content=body, status=status, headers=headers)
            return response

        return self.get_response(req)
