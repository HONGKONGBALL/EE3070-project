"""Local host controls, diagnostics, and token-protected phone location."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Protocol, TypedDict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import JsonValue
from starlette.responses import Response

from ..app_types import ControlResult, SelectionResult, Status, TextResult
from .access import DiagnosticsAccess
from .schemas import ControlRequest, DestinationSelection, Fix, ScenarioRequest, SpokenText

STATIC: Final = Path(__file__).parent / "static"
ASSETS: Final = frozenset({"dashboard.css", "dashboard.js", "dashboard-render.js", "dashboard-dom.js", "dashboard-replay.js"})
PAGE_HEADERS: Final = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
}


class Controller(Protocol):
    def status(self) -> Status | Mapping[str, JsonValue]: ...
    def log_files(self) -> list[Path]: ...
    def on_location(self, lng: float, lat: float, accuracy: float) -> None: ...
    def on_destination_text(self, text: str) -> TextResult | Mapping[str, JsonValue]: ...
    def select_destination(self, search_id: str, candidate_id: str) -> SelectionResult: ...
    def enqueue_control(self, action: str) -> ControlResult | Mapping[str, JsonValue]: ...
    def enqueue_scenario(self, name: str) -> Mapping[str, JsonValue]: ...
    def camera_frame(self) -> bytes | None: ...


class OriginController(Protocol):
    def search_origin(self, text: str) -> TextResult: ...
    def select_origin(self, search_id: str, candidate_id: str) -> SelectionResult: ...
    def use_phone_origin(self) -> SelectionResult: ...


class LogFile(TypedDict):
    name: str
    bytes: int


class WebSettings(Protocol):
    diag_allow: list[str]
    diag_host: str
    diag_port: int
    phone_host: str
    phone_port: int
    certfile: str
    keyfile: str


def create_app(
    ctrl: Controller, diag_allow: list[str], phone_token: str | None, *,
    origin_control: OriginController | None = None,
) -> FastAPI:
    access = DiagnosticsAccess.from_allowlist(diag_allow)
    app = FastAPI(title="EE3070 cane diagnostics", docs_url=None, redoc_url=None, openapi_url=None)

    def check_token(token: str | None) -> None:
        if not phone_token:
            raise HTTPException(403, "phone location disabled (CANE_PHONE_TOKEN not set)")
        if not token or not hmac.compare_digest(token.encode(), phone_token.encode()):
            raise HTTPException(403, "bad token")

    # ------------------------------------------------------- diagnostics
    @app.get("/", response_class=HTMLResponse)
    def diag_page(request: Request) -> HTMLResponse:
        access.require_client(request)
        return HTMLResponse((STATIC / "diag.html").read_text(encoding="utf-8"), headers=PAGE_HEADERS)

    @app.get("/assets/{name}")
    def asset(name: str, request: Request) -> FileResponse:
        access.require_client(request)
        if name not in ASSETS:
            raise HTTPException(404, "no such asset")
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    # Keep additive host fields in Status subclasses; do not filter by the core schema.
    @app.get("/api/status", response_model=None)
    def status(request: Request) -> Status | Mapping[str, JsonValue]:
        access.require_client(request)
        return ctrl.status()

    @app.get("/api/logs")
    def logs(request: Request) -> list[LogFile]:
        access.require_client(request)
        return [{"name": p.name, "bytes": p.stat().st_size} for p in ctrl.log_files()]

    @app.get("/api/camera/frame.jpg")
    def camera_frame(request: Request) -> Response:
        access.require_client(request)
        frame = ctrl.camera_frame()
        if frame is None:
            raise HTTPException(503, "no camera frame is available")
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/logs/{name}")
    def log_file(name: str, request: Request) -> FileResponse:
        access.require_client(request)
        for p in ctrl.log_files():
            if p.name == name:  # only files the logbook lists; no path traversal
                return FileResponse(p, filename=p.name, media_type="text/plain")
        raise HTTPException(404, "no such log")

    @app.post("/api/voice/text", response_model=None)
    def voice_text(body: SpokenText, request: Request) -> TextResult | Mapping[str, JsonValue]:
        """Same path as recognised speech: match -> read back -> wait for OK button."""
        access.require_control(request)
        return ctrl.on_destination_text(body.text)

    @app.post("/api/voice/select", response_model=None)
    def voice_select(body: DestinationSelection, request: Request) -> SelectionResult:
        """Accept a single explicit choice from the current results."""
        access.require_control(request)
        return ctrl.select_destination(body.search_id, body.candidate_id)

    if origin_control is not None:
        origin = origin_control

        @app.post("/api/voice/origin/search", response_model=None)
        def search_origin(body: SpokenText, request: Request) -> TextResult:
            access.require_control(request)
            return origin.search_origin(body.text)

        @app.post("/api/voice/origin/select", response_model=None)
        def select_origin(body: DestinationSelection, request: Request) -> SelectionResult:
            access.require_control(request)
            return origin.select_origin(body.search_id, body.candidate_id)

        @app.post("/api/voice/origin/phone", response_model=None)
        def phone_origin(request: Request) -> SelectionResult:
            access.require_control(request)
            return origin.use_phone_origin()

    @app.post("/api/control", response_model=None)
    def control(body: ControlRequest, request: Request) -> ControlResult | Mapping[str, JsonValue]:
        access.require_control(request)
        return ctrl.enqueue_control(body.action)

    @app.post("/api/sim/scenario")
    def scenario(body: ScenarioRequest, request: Request) -> Mapping[str, JsonValue]:
        access.require_control(request)
        if ctrl.status().get("sim") is not True:
            raise HTTPException(409, "simulation scenarios are unavailable on a real host")
        return ctrl.enqueue_scenario(body.name)

    # ------------------------------------------------------------- phone
    @app.get("/phone", response_class=HTMLResponse)
    def phone_page(token: str | None = None) -> HTMLResponse:
        check_token(token)
        return HTMLResponse((STATIC / "phone.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @app.post("/api/phone/location")
    def phone_location(fix: Fix) -> Mapping[str, JsonValue]:
        check_token(fix.token)
        ctrl.on_location(fix.lng, fix.lat, fix.accuracy)
        return {"ok": True}

    return app


def serve(ctrl: Controller, web_cfg: WebSettings, phone_token: str | None) -> None:
    """Start uvicorn in background threads: diagnostics over HTTP on localhost,
    phone page over HTTPS on the hotspot interface (if a certificate exists)."""
    import logging
    import threading

    import uvicorn

    app = create_app(ctrl, web_cfg.diag_allow, phone_token)
    servers = [uvicorn.Config(app, host=web_cfg.diag_host, port=web_cfg.diag_port, log_level="warning", proxy_headers=False)]
    if Path(web_cfg.certfile).exists() and Path(web_cfg.keyfile).exists():
        servers.append(uvicorn.Config(app, host=web_cfg.phone_host, port=web_cfg.phone_port, log_level="warning",
                                      ssl_certfile=web_cfg.certfile, ssl_keyfile=web_cfg.keyfile, proxy_headers=False))
    else:
        logging.getLogger(__name__).warning("no TLS certificate (%s); phone page disabled - run tools/make_cert.sh",
                                            web_cfg.certfile)
    for c in servers:
        s = uvicorn.Server(c)
        threading.Thread(target=s.run, daemon=True, name=f"web-{c.port}").start()
