import http.server
import json
import os
import ssl
import sys
import threading
import time

from evidence_support import boot


def certificate():
    cert, key = sys.argv[2], sys.argv[3]
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"https://127.0.0.1:{httpd.server_address[1]}"
    hirefire, server, lines = boot(env={"DYNO": "web.1", "HIREFIRE_DATA_URL": url})
    time.sleep(2.5)
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    httpd.shutdown()
    errors = sorted({line[:150] for line in lines if line.startswith("ERROR")})
    return {"self_signed_server_received_requests": len(hits), "error_lines": errors}


def proxy():
    os.environ.update(
        {
            "HTTP_PROXY": "http://127.0.0.1:1",
            "http_proxy": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
        }
    )
    hirefire, server, lines = boot(env={"DYNO": "web.1"})
    time.sleep(2.5)
    hirefire.configuration.stop_dispatcher(flush=False)
    server.stop()
    return {
        "proxy_variables_point_to_a_closed_port": True,
        "requests_that_reached_the_server_directly": len(server.requests),
        "error_lines": [line for line in lines if line.startswith("ERROR")][:1],
    }


if __name__ == "__main__":
    print(json.dumps(globals()[sys.argv[1]]()))
