"""Parsed MCP capabilities, route inputs and response correlation."""

from __future__ import annotations

from typing import Annotated, ClassVar, Final, Literal, override

from project import Model
from pydantic import ConfigDict, Field, JsonValue, StrictInt, StrictStr, model_validator

MAX_RESPONSE: Final = 262_144
LEGACY_VERSIONS: Final = frozenset({"2025-03-26", "2025-06-18", "2025-11-25"})
ProtocolVersion = Literal["2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"]


class ProbeError(Exception):
    """A service or input observation with a machine-readable reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        """Retain a reason code and its concrete observation."""
        self.reason: str = reason
        self.detail: str = detail
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        """Render the reason and optional observation."""
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


class RpcError(Model):
    """A JSON-RPC error with provider diagnostic data."""

    code: int
    message: str
    data: JsonValue = None


class Rpc(Model):
    """A response or request-related notification from the provider."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    jsonrpc: Literal["2.0"]
    id: StrictInt | StrictStr | None = None
    method: str | None = None
    result: dict[str, JsonValue] | None = None
    error: RpcError | None = None

    @model_validator(mode="after")
    def coherent_message(self) -> Rpc:
        """Require a notification or one correlated result/error variant."""
        if self.id is None:
            if self.method is None or self.result is not None or self.error is not None:
                message = "notification requires method"
                raise ValueError(message)
        elif self.method is not None or (self.result is None) == (self.error is None):
            message = "response requires one result or error"
            raise ValueError(message)
        return self


class RouteInput(Model):
    """One operator-confirmed route in the Provider coordinate system."""

    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    origin: str
    destination: str
    coord_frame: Literal["WGS84", "GCJ02"]
    operator_confirmed: bool = False


class Routes(Model):
    """Versioned field-route configuration."""

    schema_version: Literal[1] = 1
    routes: tuple[RouteInput, ...] = ()


class Tool(Model):
    """Discovered tool schemas retained under their wire field names."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    name: str
    input_schema: dict[str, JsonValue] = Field(alias="inputSchema")
    output_schema: dict[str, JsonValue] | None = Field(default=None, alias="outputSchema")


class Tools(Model):
    """A page of discovered tool descriptions."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    tools: tuple[Tool, ...]
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class RpcCall(Model):
    """One MCP invocation and its schema annotation context."""

    method: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    notification: bool = False
    tool: Tool | None = None


class PreparedRequest(Model):
    """Wire body and headers after protocol metadata is applied."""

    body: dict[str, JsonValue]
    headers: dict[str, str]


class ProbeErrorReport(Model):
    """Evidence retained for one unsuccessful probe invocation."""

    observed_at: str
    error_type: str
    reason: str
    evidence: tuple[str, ...]


def decode_rpc(body: bytes, content_type: str, expected_id: int) -> Rpc:
    """Decode JSON/SSE and require the request-correlated final response."""
    if len(body) > MAX_RESPONSE:
        msg = "ROUTE_CAPACITY"
        raise ProbeError(msg)
    if "text/event-stream" in content_type:
        events = body.decode().replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
        for event in events:
            data = "\n".join(
                line[5:].removeprefix(" ")
                for line in event.splitlines()
                if line.startswith("data:")
            )
            if not data.strip():
                continue
            rpc = Rpc.model_validate_json(data)
            if rpc.id is None:
                continue
            if rpc.id != expected_id:
                msg = "REQUEST_ID_MISMATCH"
                raise ProbeError(msg)
            return rpc
        msg = "RPC_RESPONSE_REQUIRED"
        raise ProbeError(msg)
    if "application/json" not in content_type:
        msg = "CONTENT_TYPE"
        raise ProbeError(msg)
    rpc = Rpc.model_validate_json(body)
    if rpc.id != expected_id:
        msg = "REQUEST_ID_MISMATCH"
        raise ProbeError(msg)
    return rpc
