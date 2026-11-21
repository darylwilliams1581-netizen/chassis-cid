"""Local-only HTTP interface. Binds to 127.0.0.1 and nowhere else."""

from __future__ import annotations

import json
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from drayl_chat import __label__

if TYPE_CHECKING:
    from drayl_chat.app import ChatBox

HOST = "127.0.0.1"
DEFAULT_PORT = 8790
MAX_BODY = 64 * 1024
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


def _pick_port(preferred: int) -> int:
    candidates = [preferred] + list(range(preferred + 1, preferred + 11))
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, port))
            except OSError:
                continue
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def make_handler(box: "ChatBox"):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            sys_stderr_write = self.server.log  # type: ignore[attr-defined]
            sys_stderr_write("%s - %s\n" % (self.address_string(), format % args))

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict | list) -> None:
            body = json.dumps(payload).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            if length > MAX_BODY:
                raise ValueError("body_too_large")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected_object")
            return data

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                html = INDEX_FILE.read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
                return
            if path == "/api/status":
                self._send_json(200, box.status())
                return
            if path == "/api/heatmap":
                self._send_json(200, box.heatmap.aggregates())
                return
            if path == "/api/staging":
                self._send_json(200, {"candidates": box.list_staging()})
                return
            self._send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid_json"})
                return

            if path == "/api/chat":
                text = str(payload.get("text") or "")
                result = box.chat(text)
                status = 200 if result.get("ok") else 400
                self._send_json(status, result)
                return
            if path == "/api/sleep":
                self._send_json(200, box.sleep())
                return
            if path == "/api/promote":
                candidate_id = payload.get("id")
                passing = bool(payload.get("passing") or payload.get("all_passing"))
                if passing or not candidate_id:
                    self._send_json(200, box.promote(passing=True))
                else:
                    self._send_json(200, box.promote(candidate_id=str(candidate_id)))
                return
            self._send_json(404, {"error": "not_found"})

    return Handler


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, log_file):
        self._log_file = log_file
        super().__init__(addr, handler)

    def server_bind(self) -> None:
        # Refuse to listen on anything but loopback.
        host, port = self.server_address
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError(f"refusing to bind to {host}; localhost only")
        super().server_bind()

    def log(self, message: str) -> None:
        self._log_file.write(message)
        self._log_file.flush()


def serve(box: "ChatBox", port: int = DEFAULT_PORT, open_browser: bool = True, log_file=None) -> None:
    import sys

    if log_file is None:
        log_file = sys.stderr
    chosen = _pick_port(port)
    server = QuietServer((HOST, chosen), make_handler(box), log_file)
    url = f"http://{HOST}:{chosen}/"
    print(f"{__label__} is running at {url}")
    print("This stays on your computer only (127.0.0.1). Press Ctrl+C to stop.")
    if open_browser:
        # Opening a local URL in the default browser. No outbound API call.
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.shutdown()
        server.server_close()


def start_background(box: "ChatBox", port: int = 0) -> tuple[QuietServer, int]:
    """Used by tests. Does not open a browser."""
    import sys

    chosen = _pick_port(port) if port else 0
    server = QuietServer((HOST, chosen), make_handler(box), sys.stderr)
    thread = threading.Thread(target=server.serve_forever, name="drayl-http", daemon=True)
    thread.start()
    return server, int(server.server_address[1])
