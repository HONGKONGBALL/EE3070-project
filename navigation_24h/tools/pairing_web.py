#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2.12,<3", "pyserial>=3.5,<4", "typer>=0.16,<1"]
# ///
# ─── How to run ───
# uv run --project navigation_24h python navigation_24h/tools/pairing_web.py --port PORT
# ──────────────────
"""Loopback browser confirmation for the current USB pairing comparison."""

from __future__ import annotations

import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from typing import Annotated, ClassVar, Final, override
from uuid import uuid4

import typer
from pairing_models import PairingError
from pairing_web_page import CSS, HTML, JS
from pairing_web_state import ACTION_ADAPTER, WebController
from pairing_web_worker import serial_owner
from project import ROOT, config
from pydantic import ValidationError

MAX_BODY: Final = 2048
CSP: Final = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def handler_for(state: WebController, token: str, origin: str) -> type[BaseHTTPRequestHandler]:  # noqa: C901 - Closure scopes independently bounded HTTP methods.
    """Bind a strict origin and fragment-delivered token to one loopback server."""
    expected_host = origin.removeprefix("http://")

    class Handler(BaseHTTPRequestHandler):
        """Serve fixed assets and validate browser intents at the HTTP boundary."""

        timeout: ClassVar[float | None] = 2

        def _response(self, code: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            _ = self.wfile.write(body)

        def _valid(self, *, action: bool = False, api: bool = False) -> bool:
            if self.client_address[0] != "127.0.0.1":
                return False
            if self.headers.get_all("Host", []) != [expected_host]:
                return False
            origins = self.headers.get_all("Origin", [])
            if action and origins != [origin]:
                return False
            if origins and origins != [origin]:
                return False
            if api:
                tokens = self.headers.get_all("X-Pairing-Token", [])
                if len(tokens) != 1 or not secrets.compare_digest(tokens[0], token):
                    return False
            return True

        def _error(self, code: HTTPStatus) -> None:
            self._response(
                code, b'{"message":"refresh current confirmation page"}', "application/json"
            )

        def do_GET(self) -> None:
            """Return fixed page assets or the token-protected immutable state."""
            if not self._valid(api=self.path == "/api/state"):
                self._error(HTTPStatus.FORBIDDEN)
                return
            assets = {
                "/": (HTML, "text/html; charset=utf-8"),
                "/app.js": (JS, "text/javascript; charset=utf-8"),
                "/app.css": (CSS, "text/css; charset=utf-8"),
            }
            asset = assets.get(self.path)
            if asset is not None:
                self._response(HTTPStatus.OK, asset[0].encode(), asset[1])
            elif self.path == "/api/state":
                self._response(
                    HTTPStatus.OK, state.view().model_dump_json().encode(), "application/json"
                )
            else:
                self._error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            """Queue one validated action while the serial owner keeps polling."""
            if self.path != "/api/action" or not self._valid(action=True, api=True):
                self._error(HTTPStatus.FORBIDDEN)
                return
            lengths = self.headers.get_all("Content-Length", [])
            if (
                len(lengths) != 1
                or not lengths[0].isdigit()
                or self.headers.get("Transfer-Encoding")
            ):
                self._error(HTTPStatus.BAD_REQUEST)
                return
            size = int(lengths[0])
            if not 0 < size <= MAX_BODY:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            if self.headers.get("Content-Type") != "application/json":
                self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return
            try:
                action = ACTION_ADAPTER.validate_json(self.rfile.read(size))
                state.enqueue(action)
            except (ValidationError, PairingError, OSError):
                self._error(HTTPStatus.CONFLICT)
                return
            self._response(HTTPStatus.ACCEPTED, b'{"queued":true}', "application/json")

        @override
        def log_message(self, format: str, *_args: str) -> None:
            """Keep HTTP credentials in their request-only boundary."""
            _ = format

    return Handler


def create_server(state: WebController, token: str, port: int = 0) -> ThreadingHTTPServer:
    """Bind an ephemeral IPv4 loopback port before constructing the handler."""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    server.RequestHandlerClass = handler_for(state, token, f"http://127.0.0.1:{server.server_port}")
    return server


def main(port: Annotated[str, typer.Option()] = "") -> None:
    """Run the USB owner thread and print the private local confirmation URL."""
    selected = port or config().esp32.port
    if not selected:
        message = "请指定 ESP32 USB 串口"
        raise typer.BadParameter(message)
    folder = ROOT / "evidence/T10" / ("pairing_web_" + uuid4().hex[:12])
    folder.mkdir(parents=True, exist_ok=False)
    state, stop = WebController(), Event()
    token = secrets.token_urlsafe(32)
    worker = Thread(target=serial_owner, args=(selected, folder, state, stop), daemon=True)
    with create_server(state, token) as server:
        worker.start()
        print(f"本机确认页：http://127.0.0.1:{server.server_port}/#{token}", flush=True)
        print(f"配对证据：{folder}", flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            stop.set()
        finally:
            stop.set()
            worker.join(timeout=5)


if __name__ == "__main__":
    typer.run(main)
