from hirefire_resource.middleware import process_request_queue_time
from hirefire_resource.middleware.wsgi import request_start_from_environ


class HireFireMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        process_request_queue_time(request_start_from_environ(request.META))
        return self.get_response(request)
