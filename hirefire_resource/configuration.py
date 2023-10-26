from autoscale_agent.web_dispatcher import WebDispatcher
from autoscale_agent.worker_dispatcher import WorkerDispatcher
from autoscale_agent.worker_dispatchers import WorkerDispatchers
from autoscale_agent.worker_server import WorkerServer
from autoscale_agent.worker_servers import WorkerServers


class InvalidPlatformError(Exception):
    pass


class Configuration:
    def __init__(self, platform=None):
        self.platform = self._validate_platform(platform)
        self.web_dispatcher = None
        self.worker_dispatchers = WorkerDispatchers()
        self.worker_servers = WorkerServers()

    def dispatch(self, token, block=None):
        if block:
            self.worker_dispatchers.append(WorkerDispatcher(token, block))
        else:
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
