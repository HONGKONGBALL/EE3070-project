"""Replay recorded place responses through the production MCP transport."""

from __future__ import annotations

import sys
from io import BytesIO, TextIOWrapper
from typing import ClassVar

import httpx2
import poi_search
import pytest
from poi_search import PlacesClient, PlaceSearchError
from probe_transport import ProbeTransport
from project import ROOT, MCPConfig, Model
from provider_models import Rpc
from pydantic import ConfigDict, JsonValue


class SavedExchange(Model):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    http_status: int
    content_type: str
    raw_response: str


class OutgoingCall(Model):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    id: int | None = None
    method: str
    params: dict[str, JsonValue]


def response_from_capture(folder: str, exchange: int, request: OutgoingCall) -> httpx2.Response:
    file = ROOT / "fixtures/poi" / folder / f"exchange_{exchange:03}.json"
    capture = SavedExchange.model_validate_json(file.read_text())
    if request.id is None:
        return httpx2.Response(capture.http_status)
    response = Rpc.model_validate_json(capture.raw_response).model_copy(update={"id": request.id})
    return httpx2.Response(
        capture.http_status,
        headers={"content-type": capture.content_type},
        content=response.model_dump_json(exclude_none=True),
    )


def test_owner_can_turn_off_capture_when_replaying_a_request() -> None:
    # Given: a replay transport configured to keep the caller's request in memory.
    def replay(http_request: httpx2.Request) -> httpx2.Response:
        call = OutgoingCall.model_validate_json(http_request.content)
        exchange = 1 if call.method == "initialize" else 2
        return response_from_capture("search", exchange, call)

    with httpx2.Client(transport=httpx2.MockTransport(replay)) as http:
        session = ProbeTransport(http, MCPConfig(), "fixture-api-key", evidence_dir=None)
        # When: the normal session handshake runs.
        _ = session.start()
    # Then: the session negotiated a version and produced no exchange capture.
    assert session.protocol_version == "2025-03-26"
    assert session.exchanges == []


def test_provider_capture_returns_five_candidates_in_rank_order() -> None:
    # Given: the captured Hong Kong campus keyword search.
    def replay(http_request: httpx2.Request) -> httpx2.Response:
        call = OutgoingCall.model_validate_json(http_request.content)
        assert call.method == "tools/call"
        assert call.params == {
            "name": "maps_text_search",
            "arguments": {"keywords": "香港城市大学", "city": "香港", "citylimit": True},
        }
        return response_from_capture("search", 3, call)

    with httpx2.Client(transport=httpx2.MockTransport(replay)) as http:
        session = ProbeTransport(http, MCPConfig(), "fixture-api-key", evidence_dir=None)
        # When: the caller asks for campus candidates.
        choices = PlacesClient(session).search(" 香港城市大学 ", "香港")
    # Then: the top five contain the expected first ranked name, ID and address.
    assert len(choices) == 5
    assert choices[0].provider_id == "B073C03EUV"
    assert choices[0].name == "香港城市大学(南区)"
    assert choices[0].address == "达之路83号"


def test_selected_detail_keeps_coordinates_tagged_with_the_provider_frame() -> None:
    # Given: a saved detail response for the selected entrance ID.
    def replay(http_request: httpx2.Request) -> httpx2.Response:
        call = OutgoingCall.model_validate_json(http_request.content)
        assert call.params == {"name": "maps_search_detail", "arguments": {"id": "B0HUY9TTL6"}}
        return response_from_capture("detail", 3, call)

    with httpx2.Client(transport=httpx2.MockTransport(replay)) as http:
        session = ProbeTransport(http, MCPConfig(), "fixture-api-key", evidence_dir=None)
        # When: the app requests just the selected POI detail.
        detail = PlacesClient(session).detail("B0HUY9TTL6")
    # Then: the entrance retains its ID and the untouched provider coordinates.
    assert detail.provider_id == "B0HUY9TTL6"
    assert detail.coordinate_frame == "GCJ02"
    assert (detail.lng, detail.lat) == (114.177692, 22.332921)


def test_failed_provider_search_raises_a_stable_error() -> None:
    # Given: a standardized MCP tool error with provider text.
    def replay(http_request: httpx2.Request) -> httpx2.Response:
        call = OutgoingCall.model_validate_json(http_request.content)
        return httpx2.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": call.id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "provider error text"}],
                },
            },
        )

    with httpx2.Client(transport=httpx2.MockTransport(replay)) as http:
        session = ProbeTransport(http, MCPConfig(), "fixture-api-key", evidence_dir=None)
        # When / Then: the unsuccessful lookup exposes only its stable error class.
        with pytest.raises(PlaceSearchError, match=r"^PLACE_PROVIDER$"):
            _ = PlacesClient(session).search("香港城市大学")


def test_detail_rejects_a_different_provider_id() -> None:
    # Given: a valid detail reply about a different place.
    def replay(http_request: httpx2.Request) -> httpx2.Response:
        call = OutgoingCall.model_validate_json(http_request.content)
        return response_from_capture("detail", 3, call)

    with httpx2.Client(transport=httpx2.MockTransport(replay)) as http:
        session = ProbeTransport(http, MCPConfig(), "fixture-api-key", evidence_dir=None)
        # When / Then: the mismatched place cannot become the selected detail.
        with pytest.raises(PlaceSearchError, match=r"^PLACE_ID_MISMATCH$"):
            _ = PlacesClient(session).detail("DIFFERENT_ID")


@pytest.mark.parametrize(
    ("request_body", "kind", "folder", "tool"),
    [
        (
            b'{"query":"campus","city":"Hong Kong"}',
            "candidates",
            "search",
            "maps_text_search",
        ),
        (b'{"id":"B0HUY9TTL6"}', "place", "detail", "maps_search_detail"),
    ],
)
def test_worker_returns_canonical_json_for_recorded_amap_responses(
    request_body: bytes,
    kind: str,
    folder: str,
    tool: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the command's stdin and captured provider replies in its normal session sequence.
    def replay(http_request: httpx2.Request) -> httpx2.Response:
        assert http_request.url.host == "mcp.amap.com"
        call = OutgoingCall.model_validate_json(http_request.content)
        if call.method == "initialize":
            return response_from_capture(folder, 1, call)
        if call.method == "notifications/initialized":
            return response_from_capture(folder, 2, call)
        assert call.params["name"] == tool
        return response_from_capture(folder, 3, call)

    def create(_alias: str) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(replay))

    monkeypatch.setenv("AMAP_MAPS_API_KEY", "fixture-api-key")
    monkeypatch.setattr(poi_search, "create_client", create)
    with TextIOWrapper(BytesIO(request_body), encoding="utf-8") as stdin:
        monkeypatch.setattr(sys, "stdin", stdin)
        # When: the normal worker entry runs.
        assert poi_search.main() == 0
    # Then: its only output is a success JSON with the requested result shape.
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 1
    assert captured.out.startswith(f'{{"status":"ok","{kind}":')
    assert '"provider_id"' not in captured.out
    assert '"id"' in captured.out
    assert "fixture-api-key" not in captured.out
