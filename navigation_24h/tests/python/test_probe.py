"""Provider boundary and loopback transport scenarios."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import mcp_probe
import pytest
from mcp_probe import discover
from probe_server import Loopback, serve
from probe_transport import ProbeTransport, create_client
from project import LocalConfig, MCPConfig
from provider_models import ProbeError, Routes, RpcCall

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from project import RunRecord


@pytest.fixture
def loopback() -> Iterator[Loopback]:
    with serve() as server:
        yield server


def test_routes_accepts_versioned_empty_configuration() -> None:
    # Given: the checked-in route configuration.
    source = '{"schema_version":1,"routes":[]}'
    # When: the route boundary parses the document.
    routes = Routes.model_validate_json(source)
    # Then: its version and route collection retain their values.
    assert routes.schema_version == 1
    assert routes.routes == ()


def test_routes_rejects_unrecognized_version() -> None:
    # Given: route format version 2.
    source = '{"schema_version":2,"routes":[]}'
    # When / Then: the boundary reports its schema version validation result.
    with pytest.raises(ValueError, match="schema_version"):
        _ = Routes.model_validate_json(source)


@pytest.mark.parametrize("scenario", ["json", "sse"])
def test_real_http_decodes_correlated_reply(
    loopback: Loopback, tmp_path: Path, scenario: str
) -> None:
    local = MCPConfig(endpoint=loopback.url + "/" + scenario, protocol_version="2026-07-28")
    with create_client("synthetic") as client:
        session = ProbeTransport(client, local, "loopback-key", tmp_path)
        result = session.call(RpcCall(method="tools/list"))
    assert result.id == 1
    assert result.result is not None
    assert "tools" in result.result
    assert len(session.exchanges) == 1
    assert loopback.headers[0]["Mcp-Method"] == "tools/list"
    assert loopback.requests[0].params["_meta"] == {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "navigation-probe", "version": "0.1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def test_real_http_negotiates_legacy_version(loopback: Loopback, tmp_path: Path) -> None:
    local = MCPConfig(endpoint=loopback.url)
    with create_client("synthetic") as client:
        session = ProbeTransport(client, local, "loopback-key", tmp_path)
        _ = session.start()
        tool = discover(session, "")
    assert session.protocol_version == "2025-03-26"
    assert tool.name == "maps_direction_walking"
    assert [request.method for request in loopback.requests] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    assert loopback.headers[1]["MCP-Protocol-Version"] == "2025-03-26"
    assert loopback.headers[2]["Mcp-Session-Id"] == "loopback-session"


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("wrong_id", "REQUEST_ID_MISMATCH"),
        ("capacity", "ROUTE_CAPACITY"),
        ("rpc_error", "RPC_ERROR"),
    ],
)
def test_real_http_error_evidence(
    loopback: Loopback, tmp_path: Path, scenario: str, reason: str
) -> None:
    local = MCPConfig(endpoint=loopback.url + "/" + scenario, protocol_version="2026-07-28")
    with create_client("synthetic") as client:
        session = ProbeTransport(client, local, "loopback-key", tmp_path)
        with pytest.raises(ProbeError, match=reason):
            _ = session.call(
                RpcCall(method="tools/call", parameters={"name": "maps_direction_walking"})
            )
    assert session.exchanges[0].is_file()


def test_real_http_incompatible_version(loopback: Loopback, tmp_path: Path) -> None:
    local = MCPConfig(endpoint=loopback.url + "/unsupported")
    with create_client("synthetic") as client:
        session = ProbeTransport(client, local, "loopback-key", tmp_path)
        with pytest.raises(ProbeError, match="PROTOCOL_VERSION"):
            _ = session.start()
    assert len(loopback.requests) == 1


def test_probe_records_input_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_probe, "ROOT", tmp_path)
    monkeypatch.setattr(
        mcp_probe, "config", lambda: LocalConfig(mcp=MCPConfig(key_env="PROBE_TEST_KEY"))
    )
    monkeypatch.setenv("PROBE_TEST_KEY", "loopback-key")
    assert mcp_probe.probe(str(tmp_path / "routes.json")) == 1
    report = (tmp_path / "evidence/T03/probe_error.json").read_text()
    assert "FileNotFoundError" in report


def test_real_http_tool_error_retains_route_context(
    loopback: Loopback,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one confirmed synthetic route and a tool-error response.
    monkeypatch.setattr(mcp_probe, "ROOT", tmp_path)
    local = MCPConfig(endpoint=loopback.url + "/tool_error")
    routes = Routes.model_validate(
        {
            "schema_version": 1,
            "routes": [
                {
                    "id": "route01",
                    "origin": "114.1,22.3",
                    "destination": "114.2,22.3",
                    "coord_frame": "GCJ02",
                    "operator_confirmed": True,
                }
            ],
        }
    )
    with create_client("synthetic") as client:
        session = ProbeTransport(client, local, "loopback-key", tmp_path / "evidence")
        _ = session.start()
        tool = discover(session, "")
        # When / Then: route capture reports the tool error and preserves the raw response.
        with pytest.raises(ProbeError, match="TOOL_ERROR: route01"):
            mcp_probe.capture_routes(session, tool, routes, "GCJ02")
    assert len(session.exchanges) == 4
    assert "isError" in session.exchanges[-1].read_text()


def quiet_progress(
    _task_id: str,
    _status: str,
    _completion: int,
    _records: tuple[RunRecord, ...],
    _artifacts: tuple[str, ...],
    _next_action: str,
    _acceptance: str = "pending",
) -> None:
    return


def test_probe_captures_three_routes_over_real_http(
    loopback: Loopback,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an isolated probe workspace and three confirmed synthetic route inputs.
    local = LocalConfig(mcp=MCPConfig(endpoint=loopback.url, key_env="PROBE_TEST_KEY"))
    monkeypatch.setattr(mcp_probe, "ROOT", tmp_path)
    monkeypatch.setattr(mcp_probe, "config", lambda: local)
    monkeypatch.setattr(mcp_probe, "update", quiet_progress)
    monkeypatch.setenv("PROBE_TEST_KEY", "loopback-key")
    source = tmp_path / "routes.json"
    _ = source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routes": [
                    {
                        "id": f"route{number:02}",
                        "origin": "114.1,22.3",
                        "destination": "114.2,22.3",
                        "coord_frame": "GCJ02",
                        "operator_confirmed": True,
                    }
                    for number in range(1, 4)
                ],
            }
        )
    )
    # When: the complete probe runs through its public entry point.
    result = mcp_probe.probe(str(source))
    # Then: the observed provider and all three raw route replies are persisted.
    assert result == 0
    assert len(tuple((tmp_path / "fixtures/routes").glob("route*.json"))) == 3
    provider = (tmp_path / "fixtures/mcp/provider.json").read_text()
    assert '"protocol_version": "2025-03-26"' in provider
    assert '"inputSchema"' in provider
