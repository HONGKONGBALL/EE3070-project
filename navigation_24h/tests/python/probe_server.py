"""Local HTTP provider fixture with captured wire messages."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import TYPE_CHECKING, ClassVar, Literal, assert_never, override

from project import Model
from provider_models import MAX_RESPONSE
from pydantic import ConfigDict, Field, JsonValue

if TYPE_CHECKING:
    from collections.abc import Generator


class Incoming(Model):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    id: int | None = None
    method: Literal["initialize", "notifications/initialized", "tools/list", "tools/call"]
    params: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Loopback:
    """Local HTTP endpoint and captured request accumulator."""

    url: str
    requests: list[Incoming]
    headers: list[dict[str, str]]


def reply(incoming: Incoming, scenario: str) -> bytes:
    payload: dict[str, JsonValue] = {"jsonrpc": "2.0", "id": incoming.id}
    result: dict[str, JsonValue] = {"resultType": "complete"}
    match incoming.method:
        case "initialize":
            result.update(
                {
                    "protocolVersion": "2024-11-05" if scenario == "unsupported" else "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "synthetic"},
                }
            )
        case "tools/list":
            result["tools"] = [
                {
                    "name": "maps_direction_walking",
                    "inputSchema": {
                        "type": "object",
                        "required": ["origin", "destination"],
                        "properties": {
                            "origin": {"type": "string"},
                            "destination": {"type": "string"},
                        },
                    },
                }
            ]
        case "tools/call":
            if scenario == "rpc_error":
                payload["error"] = {"code": -32603, "message": "synthetic service error"}
                return json.dumps(payload).encode()
            result.update(
                {
                    "isError": scenario == "tool_error",
                    "structuredContent": {
                        "paths": [
                            {"distance": "100", "steps": [{"polyline": "114.1,22.3;114.2,22.3"}]}
                        ],
                    },
                }
            )
        case "notifications/initialized":
            return b""
        case unreachable:
            assert_never(unreachable)
    payload["result"] = result
    if scenario == "wrong_id":
        payload["id"] = 999
    body = json.dumps(payload).encode()
    if scenario == "capacity":
        return body.ljust(MAX_RESPONSE + 1, b" ")
    if scenario == "sse":
        progress = b'{"jsonrpc":"2.0","method":"notifications/progress"}'
        return b": keepalive\r\ndata: " + progress + b"\r\n\r\ndata: " + body + b"\r\n\r\n"
    return body


@contextmanager
def serve() -> Generator[Loopback]:
    records: list[Incoming] = []
    headers: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            incoming = Incoming.model_validate_json(
                self.rfile.read(int(self.headers["Content-Length"]))
            )
            records.append(incoming)
            headers.append(dict(self.headers.items()))
            scenario = self.path.split("?", 1)[0].strip("/")
            notification = incoming.id is None
            body = b"" if notification else reply(incoming, scenario)
            self.send_response(202 if notification else 200)
            self.send_header(
                "Content-Type", "text/event-stream" if scenario == "sse" else "application/json"
            )
            self.send_header("Content-Length", str(len(body)))
            if incoming.method == "initialize":
                self.send_header("Mcp-Session-Id", "loopback-session")
            self.end_headers()
            for offset in range(0, len(body), 13):
                _ = self.wfile.write(body[offset : offset + 13])

        @override
        def log_message(self, format: str, *args: str) -> None:
            return

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield Loopback(f"http://127.0.0.1:{server.server_port}", records, headers)
        finally:
            server.shutdown()
            thread.join(timeout=2)
