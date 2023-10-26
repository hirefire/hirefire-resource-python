from hirefire_resource.web_dispatcher import WebDispatcher
from hirefire_resource.worker_server import WorkerServer
from hirefire_resource.worker_servers import WorkerServers


class InvalidPlatformError(Exception):
    pass


class Configuration:
    def __init__(self, platform=None):
        self.platform = self._validate_platform(platform)
        self.web_dispatcher = None
        self.worker_servers = WorkerServers()

    def dispatch(self, token, block=None):
        self.web_dispatcher = WebDispatcher(token)

        return self

    def serve(self, token, block):
        self.worker_servers.append(WorkerServer(token, block))

        return self

    def _validate_platform(self, value):
        if value == "render":
            return value
        else:
            raise InvalidPlatformError(
                f"platform {value} is unsupported, "
                "'render' is currently the only supported option"
            )
