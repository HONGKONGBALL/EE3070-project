#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["httpx2[http2,brotli,zstd]>=2.16,<3", "pydantic>=2.12,<3", "jsonschema>=4.25,<5"]
# ///
# ─── How to run ───
# python3 navigation_24h/tools/navctl.py probe-mcp --routes config/field_routes.json
# ──────────────────
"""Authenticated provider discovery and traceable walking-route capture."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import httpx2
import jsonschema
from probe_transport import ProbeTransport, create_client
from progress_store import update
from project import ROOT, config, now, redact, save
from provider_models import ProbeError, ProbeErrorReport, Routes, RpcCall, Tool, Tools
from pydantic import JsonValue, ValidationError

CURSOR_LIMIT: Final = 256
ROUTE_TARGET: Final = 3


def discover(session: ProbeTransport, preferred_tool: str) -> Tool:
    """Freeze the unique walking tool across bounded paginated results."""
    cursor = ""
    seen: set[str] = set()
    matches: list[Tool] = []
    for _ in range(8):
        parameters: dict[str, JsonValue] = {"cursor": cursor} if cursor else {}
        listing = session.call(RpcCall(method="tools/list", parameters=parameters))
        tools = Tools.model_validate(listing.result)
        matches.extend(
            tool
            for tool in tools.tools
            if tool.name == preferred_tool or (not preferred_tool and "walk" in tool.name.lower())
        )
        if not tools.next_cursor:
            if len(matches) != 1:
                msg = "WALKING_SCHEMA_SELECTION"
                raise ProbeError(msg)
            tool = matches[0]
            jsonschema.Draft202012Validator.check_schema(tool.input_schema)
            return tool
        if tools.next_cursor in seen or len(tools.next_cursor) > CURSOR_LIMIT:
            msg = "TOOLS_PAGINATION"
            raise ProbeError(msg)
        cursor = tools.next_cursor
        seen.add(cursor)
    msg = "TOOLS_CAPACITY"
    raise ProbeError(msg)


def capture_routes(session: ProbeTransport, tool: Tool, routes: Routes, frame: str) -> None:
    """Persist confirmed routes with correlated provider responses."""
    for route in routes.routes:
        if not route.operator_confirmed or route.coord_frame != frame:
            msg = "ROUTE_COORDINATE_CONFIRMATION"
            raise ProbeError(msg, route.id)
        arguments: dict[str, JsonValue] = {"origin": route.origin, "destination": route.destination}
        jsonschema.validate(arguments, tool.input_schema)
        reply = session.call(
            RpcCall(
                method="tools/call",
                parameters={"name": tool.name, "arguments": arguments},
                tool=tool,
            )
        )
        if (reply.result or {}).get("isError") is True:
            msg = "TOOL_ERROR"
            raise ProbeError(msg, route.id)
        save(ROOT / "fixtures/routes" / f"{route.id}.json", reply)


def probe(routes_path: str) -> int:
    """Capture provider discovery and confirmed route samples with error evidence."""
    exchanges: list[str] = []
    key = ""
    try:
        local = config()
        key = os.environ.get(local.mcp.key_env, "")
        if not key:
            save(
                ROOT / "evidence/T03/provider_readiness.json",
                {
                    "status": "waiting_condition",
                    "service_alias": local.mcp.alias,
                    "credential_source": local.mcp.key_env,
                    "next_action": "配置本地服务凭据后运行 probe-mcp",
                },
            )
            update(
                "T03",
                "waiting_condition",
                10,
                (),
                ("tools/mcp_probe.py",),
                "配置服务凭据并探测实际工具 schema",
            )
            return 2
        source = Path(routes_path)
        if not source.is_absolute():
            source = source if source.is_file() else ROOT / source
        routes = Routes.model_validate_json(source.read_text())
        with create_client(local.mcp.alias) as client:
            session = ProbeTransport(client, local.mcp, key, ROOT / "evidence/T03")
            try:
                server = session.start()
                tool = discover(session, local.mcp.walking_tool)
                save(
                    ROOT / "fixtures/mcp/provider.json",
                    {
                        "observed_at": now(),
                        "service_alias": local.mcp.alias,
                        "protocol_version": session.protocol_version,
                        "tool": tool.model_dump(mode="json", by_alias=True),
                        "server": server.model_dump(mode="json") if server else None,
                        "evidence": [str(path.relative_to(ROOT)) for path in session.exchanges],
                        "coordinate_frame": local.mcp.coordinate_frame,
                        "coordinate_verified": local.mcp.coordinate_verified,
                        "coordinate_control_reference": local.mcp.coordinate_control_reference,
                    },
                )
                capture_routes(session, tool, routes, local.mcp.coordinate_frame)
            finally:
                exchanges.extend(str(path.relative_to(ROOT)) for path in session.exchanges)
        update(
            "T03",
            "awaiting_review" if len(routes.routes) == ROUTE_TARGET else "running",
            100 if len(routes.routes) == ROUTE_TARGET else 60,
            (),
            ("fixtures/mcp/provider.json",),
            "核对 Provider schema、香港坐标与三条路线响应",
        )
    except (
        httpx2.HTTPError,
        ProbeError,
        ValidationError,
        jsonschema.ValidationError,
        jsonschema.SchemaError,
        OSError,
        UnicodeError,
    ) as error:
        reason = redact(str(error))
        if key:
            reason = reason.replace(key, "[redacted]")
        save(
            ROOT / "evidence/T03/probe_error.json",
            ProbeErrorReport(
                observed_at=now(),
                error_type=type(error).__name__,
                reason=reason,
                evidence=tuple(exchanges),
            ),
        )
        return 1
    return 0 if len(routes.routes) == ROUTE_TARGET else 2
