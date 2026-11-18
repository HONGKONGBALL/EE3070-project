"""Bounded HTTP exchanges and MCP protocol lifecycle."""

from __future__ import annotations

import base64
import json
import logging
import socket
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import httpx2
from project import MCPConfig, now, redact, save
from provider_models import (
    LEGACY_VERSIONS,
    MAX_RESPONSE,
    PreparedRequest,
    ProbeError,
    ProtocolVersion,
    Rpc,
    RpcCall,
    decode_rpc,
)
from pydantic import JsonValue, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from pathlib import Path

LOGGER: Final = logging.getLogger(__name__)
ASCII_SPACE: Final = 0x20
ASCII_VISIBLE_MAX: Final = 0x7E
SESSION_LIMIT: Final = 256


def create_client(alias: str) -> httpx2.Client:
    """Construct the project HTTP/2 client with bounded service timeouts."""
    limits = httpx2.Limits(max_connections=200, max_keepalive_connections=40, keepalive_expiry=30)
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=2,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )

    def observe(response: httpx2.Response) -> None:
        LOGGER.info("%s HTTP %s", alias, response.status_code)

    return httpx2.Client(
        transport=transport,
        timeout=httpx2.Timeout(connect=5, read=15, write=10, pool=10),
        follow_redirects=False,
        event_hooks={"response": [observe]},
    )


def header_value(value: str) -> str:
    """Encode header values according to MCP's Base64 sentinel format."""
    safe = all(
        ASCII_SPACE <= ord(character) <= ASCII_VISIBLE_MAX or character == "\t"
        for character in value
    )
    if (
        safe
        and value == value.strip(" \t")
        and not (value.startswith("=?base64?") and value.endswith("?="))
    ):
        return value
    return "=?base64?" + base64.b64encode(value.encode()).decode("ascii") + "?="


class ProbeTransport:
    """Mutable request sequence and negotiated MCP session for one probe run."""

    def __init__(
        self, client: httpx2.Client, local: MCPConfig, key: str, evidence_dir: Path | None
    ) -> None:
        """Bind one configured provider to its HTTP client and evidence directory."""
        self.client: httpx2.Client = client
        self.local: MCPConfig = local
        self.key: str = key
        self.protocol_version: str = local.protocol_version
        self.session_id: str = ""
        self.sequence: int = 0
        self.evidence_dir: Path | None = (
            None if evidence_dir is None else evidence_dir / uuid4().hex[:12]
        )
        self.exchanges: list[Path] = []

    def start(self) -> Rpc | None:
        """Negotiate a legacy session or start the configured modern lifecycle."""
        if self.protocol_version not in LEGACY_VERSIONS:
            return None
        server = self.call(
            RpcCall(
                method="initialize",
                parameters={
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "navigation-probe", "version": "0.1"},
                },
            )
        )
        negotiated = (server.result or {}).get("protocolVersion")
        try:
            version = TypeAdapter(ProtocolVersion).validate_python(negotiated)
        except ValidationError as error:
            msg = "PROTOCOL_VERSION"
            raise ProbeError(msg, str(negotiated)) from error
        if version not in LEGACY_VERSIONS:
            msg = "PROTOCOL_VERSION"
            raise ProbeError(msg, str(negotiated))
        self.protocol_version = version
        _ = self.call(RpcCall(method="notifications/initialized", notification=True))
        return server

    def _prepare(self, invocation: RpcCall) -> PreparedRequest:
        """Apply the current session, version and per-request metadata."""
        self.sequence += 1
        parameters = dict(invocation.parameters)
        request: dict[str, JsonValue] = {
            "jsonrpc": "2.0",
            "method": invocation.method,
            "params": parameters,
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if not invocation.notification:
            request["id"] = self.sequence
        if self.session_id and invocation.method != "initialize":
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version == "2026-07-28":
            parameters["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": self.protocol_version,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "navigation-probe",
                    "version": "0.1",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            }
            headers["Mcp-Method"] = invocation.method
            if invocation.method == "tools/call":
                headers["Mcp-Name"] = header_value(str(parameters["name"]))
        return PreparedRequest(body=request, headers=headers)

    def call(self, invocation: RpcCall) -> Rpc:
        """Send one JSON-RPC message and retain its bounded raw exchange."""
        prepared = self._prepare(invocation)
        begin = time.monotonic_ns()
        with self.client.stream(
            "POST",
            self.local.endpoint,
            params={"key": self.key},
            headers=prepared.headers,
            content=json.dumps(prepared.body),
        ) as response:
            data = bytearray()
            capacity = False
            for chunk in response.iter_bytes():
                available = MAX_RESPONSE + 1 - len(data)
                data.extend(chunk[:available])
                if len(data) > MAX_RESPONSE:
                    capacity = True
                    break
            self._capture(invocation, prepared, response, data, begin, capacity=capacity)
            if capacity:
                msg = "ROUTE_CAPACITY"
                raise ProbeError(msg)
            _ = response.raise_for_status()
            if invocation.notification:
                if response.status_code != HTTPStatus.ACCEPTED or data:
                    msg = "NOTIFICATION_ACCEPTANCE"
                    raise ProbeError(msg)
                return Rpc(jsonrpc="2.0", method=invocation.method)
            rpc = decode_rpc(bytes(data), response.headers.get("content-type", ""), self.sequence)
            if rpc.error is not None:
                msg = "RPC_ERROR"
                raise ProbeError(msg, f"{rpc.error.code}: {rpc.error.message}")
            if invocation.method == "initialize":
                session = response.headers.get("Mcp-Session-Id", "")
                if len(session) > SESSION_LIMIT or any(
                    not ASCII_SPACE < ord(c) <= ASCII_VISIBLE_MAX for c in session
                ):
                    msg = "SESSION_HEADER"
                    raise ProbeError(msg)
                self.session_id = session
            elif (
                self.protocol_version == "2026-07-28"
                and (rpc.result or {}).get("resultType") != "complete"
            ):
                msg = "RESULT_TYPE"
                raise ProbeError(msg)
            return rpc

    def _capture(
        self,
        invocation: RpcCall,
        prepared: PreparedRequest,
        response: httpx2.Response,
        data: bytearray,
        begin: int,
        *,
        capacity: bool,
    ) -> None:
        if self.evidence_dir is None:
            return
        evidence = self.evidence_dir / f"exchange_{self.sequence:03}.json"
        save(
            evidence,
            {
                "method": invocation.method,
                "request_id": self.sequence,
                "http_status": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "duration_ms": (time.monotonic_ns() - begin) / 1_000_000,
                "request": prepared.body,
                "raw_response": redact(data.decode(errors="replace")).replace(
                    self.key, "[redacted]"
                ),
                "capture_status": "ROUTE_CAPACITY" if capacity else "captured",
                "observed_at": now(),
            },
        )
        self.exchanges.append(evidence)
