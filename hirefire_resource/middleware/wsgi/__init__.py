# WSGI middleware adapters (Flask, Django WSGI). Each adapter reads the
# X-Request-Start header from the WSGI environ and delegates to the shared
# hirefire_resource.middleware.process_request_queue_time, then passes the
# request through unchanged. The push model serves no inline endpoints.
