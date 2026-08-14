from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


class BOLALabMode(StrEnum):
    SECURE = "secure"
    VULNERABLE = "vulnerable"


ORDERS = {
    "1001": {
        "id": 1001,
        "owner": "alice",
        "description": "Alice's order",
    },
    "2001": {
        "id": 2001,
        "owner": "bob",
        "description": "Bob's order",
    },
}

TOKENS = {
    "alice-token": "alice",
    "bob-token": "bob",
}


@dataclass(frozen=True)
class RunningBOLALab:
    base_url: str
    hostname: str
    mode: BOLALabMode


def _handler_for(mode: BOLALabMode) -> type[BaseHTTPRequestHandler]:
    class BOLALabHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path_parts = self.path.split("?", 1)[0].strip("/").split("/")

            if len(path_parts) != 2 or path_parts[0] != "orders":
                self._write_json(404, {"detail": "not found"})
                return

            order = ORDERS.get(path_parts[1])

            if order is None:
                self._write_json(404, {"detail": "not found"})
                return

            authorization = self.headers.get("Authorization", "")
            scheme, separator, token = authorization.partition(" ")
            actor = TOKENS.get(token) if separator and scheme == "Bearer" else None

            if actor is None:
                self._write_json(401, {"detail": "unauthorized"})
                return

            if mode == BOLALabMode.SECURE and actor != order["owner"]:
                self._write_json(403, {"detail": "forbidden"})
                return

            self._write_json(200, order)

        def _write_json(self, status: int, body: dict[str, object]) -> None:
            encoded = json.dumps(body, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            # Keep credentials and request details out of test output.
            return

    return BOLALabHandler


@contextmanager
def run_bola_lab(mode: BOLALabMode) -> Iterator[RunningBOLALab]:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_for(mode),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host, port = server.server_address
        yield RunningBOLALab(
            base_url=f"http://{host}:{port}",
            hostname=host,
            mode=mode,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

