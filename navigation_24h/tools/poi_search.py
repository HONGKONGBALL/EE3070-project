#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["httpx2[http2,brotli,zstd]>=2.16,<3", "pydantic>=2.12,<3"]
# ///
# ─── How to run ───
# uv run --project navigation_24h python navigation_24h/tools/poi_search.py
# ──────────────────
"""Resolve destination candidates and explicit selections through AMap MCP."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from enum import StrEnum, unique
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Protocol, assert_never

import httpx2
from probe_transport import ProbeTransport, create_client
from project import MCPConfig, Model
from provider_models import ProbeError, RpcCall
from pydantic import (
    AliasChoices,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)
from secrets_config import read

if TYPE_CHECKING:
    from collections.abc import Generator

    from provider_models import Rpc
    from pydantic import JsonValue


MAX_CHOICES: Final = 5
MAX_REQUEST_BYTES: Final = 4096
AMAP_ENDPOINT: Final = "https://mcp.amap.com/mcp"
MAP_KEY: Final = "AMAP_MAPS_API_KEY"
type PlaceId = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
]
type PlaceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
type PlaceAddress = Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)]
type SearchText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]


@unique
class _Code(StrEnum):
    AUTHENTICATION = "PLACE_AUTHENTICATION"
    CAPACITY = "PLACE_CAPACITY"
    CONFIGURATION = "PLACE_CONFIGURATION"
    ID_MISMATCH = "PLACE_ID_MISMATCH"
    INPUT = "PLACE_INPUT"
    PROVIDER = "PLACE_PROVIDER"
    QUOTA = "PLACE_QUOTA"
    RESPONSE = "PLACE_RESPONSE"
    TIMEOUT = "PLACE_TIMEOUT"
    UNAVAILABLE = "PLACE_UNAVAILABLE"


class PlaceSearchError(ProbeError):
    """A stable failure code for a place lookup."""


class RpcSession(Protocol):
    """The request-correlated MCP transport used for one place session."""

    def call(self, invocation: RpcCall) -> Rpc:
        """Send one operation and return its provider response."""
        ...


class _ProviderModel(Model):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore", strict=True)


class PlaceCandidate(_ProviderModel):
    """A provider identifier and the user-visible description of one result."""

    provider_id: PlaceId = Field(
        validation_alias=AliasChoices("provider_id", "id"), serialization_alias="id"
    )
    name: PlaceName
    address: PlaceAddress = ""


class PlaceDetail(PlaceCandidate):
    """An explicit selection with its coordinates in the provider frame."""

    lng: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
    lat: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    coordinate_frame: Literal["GCJ02"] = "GCJ02"


class _ProviderDetail(PlaceCandidate):
    location: str


class _SearchReply(_ProviderModel):
    pois: tuple[PlaceCandidate, ...]


class _TextContent(_ProviderModel):
    type: Literal["text"]
    text: str


class _ToolReply(_ProviderModel):
    is_error: bool = Field(default=False, alias="isError")
    content: tuple[_TextContent, ...] = Field(default=(), strict=False, max_length=1)


class _SearchRequest(Model):
    query: SearchText
    city: SearchText | None = None


class _DetailRequest(Model):
    id: PlaceId


class _SearchOutput(Model):
    status: Literal["ok"] = "ok"
    candidates: tuple[PlaceCandidate, ...]


class _DetailOutput(Model):
    status: Literal["ok"] = "ok"
    place: PlaceDetail


class _ErrorOutput(Model):
    status: Literal["error"] = "error"
    code: str


_INPUT: Final = TypeAdapter(_SearchRequest | _DetailRequest)


@contextmanager
def _service_errors() -> Generator[None]:
    try:
        yield
    except httpx2.TimeoutException as error:
        raise PlaceSearchError(_Code.TIMEOUT) from error
    except httpx2.HTTPStatusError as error:
        status = error.response.status_code
        if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            raise PlaceSearchError(_Code.AUTHENTICATION) from error
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            raise PlaceSearchError(_Code.QUOTA) from error
        raise PlaceSearchError(_Code.PROVIDER) from error
    except httpx2.RequestError as error:
        raise PlaceSearchError(_Code.UNAVAILABLE) from error
    except ProbeError as error:
        reason = _Code.CAPACITY if error.reason == "ROUTE_CAPACITY" else _Code.PROVIDER
        raise PlaceSearchError(reason) from error
    except (ValidationError, UnicodeDecodeError) as error:
        raise PlaceSearchError(_Code.RESPONSE) from error


class PlacesClient:
    """Look up candidates and resolve the one explicitly selected by the caller."""

    def __init__(self, session: RpcSession) -> None:
        """Use an active provider session for lightweight place calls."""
        self._session: RpcSession = session

    def _tool_text(self, name: str, arguments: dict[str, JsonValue]) -> str:
        with _service_errors():
            response = self._session.call(
                RpcCall(method="tools/call", parameters={"name": name, "arguments": arguments})
            )
        if response.error is not None:
            raise PlaceSearchError(_Code.PROVIDER)
        try:
            reply = _ToolReply.model_validate(response.result)
        except ValidationError as error:
            raise PlaceSearchError(_Code.RESPONSE) from error
        if reply.is_error:
            raise PlaceSearchError(_Code.PROVIDER)
        if not reply.content:
            raise PlaceSearchError(_Code.RESPONSE)
        return reply.content[0].text

    def search(self, keyword: str, city: str | None = None) -> tuple[PlaceCandidate, ...]:
        """Return the highest ranked unique candidates in provider order."""
        try:
            request = _SearchRequest(query=keyword, city=city)
        except ValidationError as error:
            raise PlaceSearchError(_Code.INPUT) from error
        arguments: dict[str, JsonValue] = {"keywords": request.query}
        if request.city is not None:
            arguments.update(city=request.city, citylimit=True)
        payload = self._tool_text("maps_text_search", arguments)
        try:
            matches = _SearchReply.model_validate_json(payload).pois
        except ValidationError as error:
            raise PlaceSearchError(_Code.RESPONSE) from error
        unique: dict[str, PlaceCandidate] = {}
        for place in matches:
            _ = unique.setdefault(place.provider_id, place)
            if len(unique) == MAX_CHOICES:
                break
        return tuple(unique.values())

    def detail(self, provider_id: str) -> PlaceDetail:
        """Fetch a selected candidate's provider coordinates with its frame tag."""
        try:
            request = _DetailRequest(id=provider_id)
        except ValidationError as error:
            raise PlaceSearchError(_Code.INPUT) from error
        payload = self._tool_text("maps_search_detail", {"id": request.id})
        try:
            selected = _ProviderDetail.model_validate_json(payload)
            longitude, _, latitude = selected.location.partition(",")
            detail = PlaceDetail(
                provider_id=selected.provider_id,
                name=selected.name,
                address=selected.address,
                lng=float(longitude),
                lat=float(latitude),
            )
        except ValueError as error:
            raise PlaceSearchError(_Code.RESPONSE) from error
        if detail.provider_id != request.id:
            raise PlaceSearchError(_Code.ID_MISMATCH)
        return detail


def main() -> int:
    """Read one JSON request from stdin and emit one machine-readable response."""
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        request = _INPUT.validate_json(data) if len(data) <= MAX_REQUEST_BYTES else None
    except (ValidationError, OSError):
        request = None
    if request is None:
        print(_ErrorOutput(code=_Code.INPUT).model_dump_json())
        return 2

    key = os.environ.get(MAP_KEY, "").strip()
    if env_path := os.environ.get("CANE_AMAP_SECRETS_FILE"):
        try:
            key = key or read(Path(env_path), frozenset({MAP_KEY})).get(MAP_KEY, "").strip()
        except (OSError, UnicodeError):
            key = ""
    if not key:
        print(_ErrorOutput(code=_Code.CONFIGURATION).model_dump_json())
        return 2
    settings = MCPConfig(alias="amap-places", endpoint=AMAP_ENDPOINT)
    try:
        with create_client(settings.alias) as http:
            session = ProbeTransport(http, settings, key, evidence_dir=None)
            with _service_errors():
                _ = session.start()
            places = PlacesClient(session)
            output: _SearchOutput | _DetailOutput
            match request:
                case _SearchRequest():
                    output = _SearchOutput(candidates=places.search(request.query, request.city))
                case _DetailRequest():
                    output = _DetailOutput(place=places.detail(request.id))
                case _ as unreachable:
                    assert_never(unreachable)
    except PlaceSearchError as error:
        print(_ErrorOutput(code=error.reason).model_dump_json())
        return 2
    print(output.model_dump_json(by_alias=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
