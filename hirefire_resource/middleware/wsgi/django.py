from hirefire_resource.middleware import process_request_queue_time


class HireFireMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        process_request_queue_time(request.META.get("HTTP_X_REQUEST_START"))
        return self.get_response(request)
