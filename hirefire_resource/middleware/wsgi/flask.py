from hirefire_resource.middleware import process_request_queue_time
from hirefire_resource.middleware.wsgi import request_start_from_environ


class HireFireMiddleware:
    def __init__(self, app):
        self.app = app
        self.original_wsgi_app = app.wsgi_app

    def __call__(self, environ, start_response):
        process_request_queue_time(request_start_from_environ(environ))
        return self.original_wsgi_app(environ, start_response)
