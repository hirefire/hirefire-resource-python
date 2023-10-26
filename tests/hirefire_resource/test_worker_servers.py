from autoscale_agent.worker_server import WorkerServer
from autoscale_agent.worker_servers import WorkerServers
from tests.helpers import TOKEN


def test_find():
    servers = WorkerServers()
    server = WorkerServer(TOKEN, lambda: 1.23)
    servers.append(server)
    assert servers.find(["token-a", TOKEN, "token-b"]) == server


def test_find_nothing():
    servers = WorkerServers()
    server = WorkerServer(TOKEN, lambda: 1.23)
    servers.append(server)
    assert servers.find(["token-a", "token-b"]) == None
