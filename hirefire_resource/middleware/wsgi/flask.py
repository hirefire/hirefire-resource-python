from hirefire_resource.middleware import process_request_queue_time


class HireFireMiddleware:
    def __init__(self, app):
        self.app = app
        self.original_wsgi_app = app.wsgi_app

    def __call__(self, environ, start_response):
        process_request_queue_time(environ.get("HTTP_X_REQUEST_START"))
        return self.original_wsgi_app(environ, start_response)
