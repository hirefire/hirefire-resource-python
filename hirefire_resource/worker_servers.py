class WorkerServers:
    def __init__(self):
        self._servers = []

    def append(self, server):
        self._servers.append(server)

    def find(self, tokens):
        for server in self._servers:
            if server.token in tokens:
                return server

        return None
