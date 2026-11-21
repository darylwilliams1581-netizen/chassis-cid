"""Localhost-only HTTP socket for the teacher arm.

The server binds to 127.0.0.1 only and never makes outbound calls.
External teachers are run by the user and pasted or bridged locally.
A token in a local file (generated on first run, not committed) is
required on every injection request.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from drayl.teacher import TeacherArm, parse_injections

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TOKEN_HEADER = "X-DRAYL-Token"
AUTH_HEADER = "Authorization"


class BindError(ValueError):
    """Raised when a caller asks the teacher arm to listen on a non-local address."""


def check_bind_host(host: str) -> str:
    """Refuse any bind that is not the loopback interface."""
    if host not in ALLOWED_HOSTS:
        raise BindError(
            f"teacher arm refuses to bind to {host!r}; only 127.0.0.1 (localhost) is allowed"
        )
    if host == "localhost":
        return "127.0.0.1"
    return host


def ensure_token(path: str | Path) -> str:
    """Read the local token, or create one on first run."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        token = destination.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    destination.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return token


def _extract_token(headers) -> str | None:
    raw = headers.get(TOKEN_HEADER)
    if raw:
        return raw.strip()
    auth = headers.get(AUTH_HEADER)
    if not auth:
        return None
    auth = auth.strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth


class TeacherHTTPServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, arm: TeacherArm, token: str):
        host = check_bind_host(host)
        self.arm = arm
        self.token = token
        # Force the bind address we just checked; never honour a rewritten host.
        super().__init__((host, port), TeacherHandler)

    @property
    def url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"


class TeacherHandler(BaseHTTPRequestHandler):
    server: TeacherHTTPServer

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        # Keep test output quiet. The CLI can wrap this later if needed.
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def _authorized(self) -> bool:
        got = _extract_token(self.headers)
        return bool(got) and secrets.compare_digest(got, self.server.token)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/health", "/"}:
            host, port = self.server.server_address
            self._send(
                200,
                {
                    "ok": True,
                    "service": "teacher-arm",
                    "bind": host,
                    "port": port,
                },
            )
            return
        if not self._authorized():
            self._send(401, {"status": "unauthorized", "http_status": 401})
            return
        if path == "/conflicts":
            self._send(
                200,
                {
                    "status": "ok",
                    "http_status": 200,
                    "open": self.server.arm.open_conflicts(),
                    "all": self.server.arm.all_conflicts(),
                },
            )
            return
        self._send(404, {"status": "not_found", "http_status": 404})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._authorized():
            self._send(401, {"status": "unauthorized", "http_status": 401})
            return
        try:
            payload = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"status": "bad_json", "http_status": 400, "message": str(exc)})
            return

        if path in {"/inject", "/v1/inject"}:
            try:
                injections = parse_injections(payload)
            except ValueError as exc:
                self._send(
                    400,
                    {"status": "bad_request", "http_status": 400, "message": str(exc)},
                )
                return
            result = self.server.arm.inject_many(injections)
            self._send(result.http_status, result.as_dict())
            return

        if path in {"/resolve", "/conflicts/resolve"}:
            conflict_id = payload.get("conflict_id") or payload.get("id")
            chosen_id = payload.get("chosen_id") or payload.get("grain_id") or payload.get("choose")
            if not conflict_id or not chosen_id:
                self._send(
                    400,
                    {
                        "status": "bad_request",
                        "http_status": 400,
                        "message": "conflict_id and chosen_id are required",
                    },
                )
                return
            try:
                resolved = self.server.arm.resolve(
                    str(conflict_id),
                    str(chosen_id),
                    resolved_by=str(payload.get("resolved_by") or "human"),
                )
            except (KeyError, ValueError) as exc:
                self._send(
                    400,
                    {"status": "resolve_failed", "http_status": 400, "message": str(exc)},
                )
                return
            self._send(200, {"status": "resolved", "http_status": 200, **resolved})
            return

        if path == "/drain":
            drained = self.server.arm.drain()
            self._send(
                200,
                {
                    "status": "drained",
                    "http_status": 200,
                    "batches": [item.as_dict() for item in drained],
                },
            )
            return

        self._send(404, {"status": "not_found", "http_status": 404})


def serve_forever(
    arm: TeacherArm,
    token: str,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> TeacherHTTPServer:
    server = TeacherHTTPServer(host, port, arm, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._thread = thread  # type: ignore[attr-defined]
    return server
