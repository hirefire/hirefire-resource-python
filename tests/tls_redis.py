import socket
import ssl
import subprocess
import threading


class TlsRedis:
    def __init__(self, directory):
        key, certificate = directory / "redis.key", directory / "redis.crt"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2"]
            + ["-subj", "/CN=127.0.0.1", "-keyout", str(key), "-out", str(certificate)],
            check=True,
            capture_output=True,
        )
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(certificate, key)
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.url = f"rediss://127.0.0.1:{self._listener.getsockname()[1]}/0"
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self):
        self._listener.close()

    def _serve(self):
        while True:
            try:
                connection, _address = self._listener.accept()
            except OSError:
                return
            threading.Thread(
                target=self._answer, args=(connection,), daemon=True
            ).start()

    def _answer(self, connection):
        try:
            with self._context.wrap_socket(connection, server_side=True) as secure:
                while data := secure.recv(4096):
                    secure.sendall(b"".join(map(self._reply, self._commands(data))))
        except OSError:
            pass

    @staticmethod
    def _commands(data):
        parts = data.split(b"\r\n")
        return [
            parts[index + 2].upper()
            for index, part in enumerate(parts)
            if part.startswith(b"*")
        ]

    @staticmethod
    def _reply(command):
        if command == b"HELLO":
            return b"%1\r\n$5\r\nproto\r\n:3\r\n"
        return b"+PONG\r\n" if command == b"PING" else b"+OK\r\n"
