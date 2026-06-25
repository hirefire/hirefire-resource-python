import http.client
import os
import socket
from dataclasses import dataclass
from email.message import Message
from typing import cast
from urllib.parse import urlparse

from hirefire_resource.version import VERSION


class RequestError(Exception):
    pass


@dataclass
class Response:
    status: int
    # http.client delivers response headers as an HTTPMessage (a Message subclass),
    # read case-insensitively via .get()/in.
    headers: Message


class Client:
    def __init__(self, timeout: int = 5) -> None:
        self._timeout = timeout

    def submit_samples(self, body: str) -> "Response | None":
        token = self._require_token()
        response = self._execute(
            "/metrics/ingest",
            body,
            {
                "Content-Type": "application/json",
                "HireFire-Token": token,
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

    def request_lease(self, process_id: str) -> Response:
        token = self._require_token()
        return self._execute(
            "/metrics/lease",
            "",
            {
                "HireFire-Token": token,
                "HireFire-Agent": f"Python-{VERSION}",
                "HireFire-Process-ID": process_id,
            },
        )

    def _execute(self, endpoint: str, body: str, headers: dict[str, str]) -> Response:
        uri = urlparse(self._base_url())
        connection_class: type[http.client.HTTPConnection]
        if uri.scheme == "https":
            connection_class = http.client.HTTPSConnection
        else:
            connection_class = http.client.HTTPConnection

        host = cast(str, uri.hostname)
        connection = connection_class(host, uri.port, timeout=self._timeout)
        path = uri.path.rstrip("/") + endpoint
        encoded_body = body.encode("utf-8")

        try:
            connection.request("POST", path, encoded_body, headers)
            response = connection.getresponse()
            response.read()
            return Response(response.status, response.headers)
        except (socket.timeout, TimeoutError):
            raise RequestError("Request timed out.")
        except (http.client.HTTPException, OSError) as error:
            raise RequestError(f"Network error ({type(error).__name__}: {error}).")
        finally:
            connection.close()

    def _base_url(self) -> str:
        return os.environ.get("HIREFIRE_DATA_URL", "https://data.hirefire.io")

    def _token(self) -> str | None:
        from hirefire_resource.hirefire import HireFire

        return HireFire.configuration.token

    def _require_token(self) -> str:
        token = self._token()
        if token:
            return token

        raise RequestError(
            "The HIREFIRE_TOKEN environment variable is not set.\n"
            "Set it to your HireFire token to enable metric dispatch."
        )
