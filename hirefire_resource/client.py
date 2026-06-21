import http.client
import os
import socket
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from hirefire_resource.version import VERSION


class RequestError(Exception):
    pass


@dataclass
class Response:
    status: int
    headers: Mapping


class Client:
    def __init__(self, timeout=5):
        self._timeout = timeout

    def submit_samples(self, body):
        self._require_token()
        response = self._execute(
            "/metrics/ingest",
            body,
            {
                "Content-Type": "application/json",
                "HireFire-Token": self._token(),
                "HireFire-Agent": f"Python-{VERSION}",
            },
        )

        if 200 <= response.status < 300:
            return response
        elif response.status == 401:
            return None
        elif response.status >= 500:
            raise RequestError(f"Server responded with {response.status} status.")
        else:
            raise RequestError(f"Unexpected response code {response.status}.")

    def request_lease(self, process_id):
        self._require_token()
        return self._execute(
            "/metrics/lease",
            "",
            {
                "HireFire-Token": self._token(),
                "HireFire-Agent": f"Python-{VERSION}",
                "HireFire-Process-ID": process_id,
            },
        )

    def _execute(self, endpoint, body, headers):
        uri = urlparse(self._base_url())
        if uri.scheme == "https":
            connection_class = http.client.HTTPSConnection
        else:
            connection_class = http.client.HTTPConnection

        connection = connection_class(uri.hostname, uri.port, timeout=self._timeout)
        path = uri.path.rstrip("/") + endpoint
        if isinstance(body, str):
            body = body.encode("utf-8")

        try:
            connection.request("POST", path, body, headers)
            response = connection.getresponse()
            response.read()
            return Response(response.status, response.headers)
        except (socket.timeout, TimeoutError):
            raise RequestError("Request timed out.")
        except (http.client.HTTPException, OSError) as error:
            raise RequestError(f"Network error ({type(error).__name__}: {error}).")
        finally:
            connection.close()

    def _base_url(self):
        return os.environ.get("HIREFIRE_DATA_URL", "https://data.hirefire.io")

    def _token(self):
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.token

    def _require_token(self):
        if self._token():
            return

        raise RequestError(
            "The HIREFIRE_TOKEN environment variable is not set.\n"
            "Set it to your HireFire token to enable metric dispatch."
        )
