"""Diagnostics (PRD F16) + phone location endpoint.

Diagnostics: team members only. Read-only status, log download, test-mode
indicator, and a text box that feeds the spoken-destination path (for testing
without the mic). There is deliberately NO endpoint that can clear a safety
veto, silence a safety prompt, or change thresholds, and no response contains
API keys. Diagnostics routes only answer clients in `web.diag_allow`
(default: localhost only).

Phone: the phone that runs the hotspot opens /phone over HTTPS (browsers only
allow geolocation on secure origins) and posts fixes with the shared token
from CANE_PHONE_TOKEN. Without a token configured, phone routes are disabled.
"""

from __future__ import annotations

import hmac
import ipaddress
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

STATIC = Path(__file__).parent / "static"


class Controller(Protocol):
    def status(self) -> dict: ...
    def log_files(self) -> list[Path]: ...
    def on_location(self, lng: float, lat: float, accuracy: float) -> None: ...
    def on_destination_text(self, text: str) -> dict: ...


class Fix(BaseModel):
    lng: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    accuracy: float = Field(default=50.0, ge=0)
    token: str


class SpokenText(BaseModel):
    text: str = Field(max_length=200)


def create_app(ctrl: Controller, diag_allow: list[str], phone_token: str | None) -> FastAPI:
    nets = [ipaddress.ip_network(n, strict=False) for n in diag_allow]
    app = FastAPI(title="EE3070 cane diagnostics", docs_url=None, redoc_url=None, openapi_url=None)

    def require_diag(request: Request) -> None:
        host = request.client.host if request.client else ""
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise HTTPException(403, "diagnostics not allowed from this address")
        if not any(ip in n for n in nets):
            raise HTTPException(403, "diagnostics not allowed from this address")

    def check_token(token: str | None) -> None:
        if not phone_token:
            raise HTTPException(403, "phone location disabled (CANE_PHONE_TOKEN not set)")
        if not token or not hmac.compare_digest(token.encode(), phone_token.encode()):
            raise HTTPException(403, "bad token")

    # ------------------------------------------------------- diagnostics
    @app.get("/", response_class=HTMLResponse)
    def diag_page(request: Request):
        require_diag(request)
        return (STATIC / "diag.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    def status(request: Request):
        require_diag(request)
        return ctrl.status()

    @app.get("/api/logs")
    def logs(request: Request):
        require_diag(request)
        return [{"name": p.name, "bytes": p.stat().st_size} for p in ctrl.log_files()]

    @app.get("/api/logs/{name}")
    def log_file(name: str, request: Request):
        require_diag(request)
        for p in ctrl.log_files():
            if p.name == name:  # only files the logbook lists; no path traversal
                return FileResponse(p, filename=p.name, media_type="text/plain")
        raise HTTPException(404, "no such log")

    @app.post("/api/voice/text")
    def voice_text(body: SpokenText, request: Request):
        """Same path as recognised speech: match -> read back -> wait for OK button."""
        require_diag(request)
        return ctrl.on_destination_text(body.text)

    # ------------------------------------------------------------- phone
    @app.get("/phone", response_class=HTMLResponse)
    def phone_page(token: str | None = None):
        check_token(token)
        return (STATIC / "phone.html").read_text(encoding="utf-8")

    @app.post("/api/phone/location")
    def phone_location(fix: Fix):
        check_token(fix.token)
        ctrl.on_location(fix.lng, fix.lat, fix.accuracy)
        return {"ok": True}

    return app


def serve(ctrl: Controller, web_cfg, phone_token: str | None) -> None:
    """Start uvicorn in background threads: diagnostics over HTTP on localhost,
    phone page over HTTPS on the hotspot interface (if a certificate exists)."""
    import logging
    import threading

    import uvicorn

    app = create_app(ctrl, web_cfg.diag_allow, phone_token)
    servers = [uvicorn.Config(app, host=web_cfg.diag_host, port=web_cfg.diag_port, log_level="warning")]
    if Path(web_cfg.certfile).exists() and Path(web_cfg.keyfile).exists():
        servers.append(uvicorn.Config(app, host=web_cfg.phone_host, port=web_cfg.phone_port, log_level="warning",
                                      ssl_certfile=web_cfg.certfile, ssl_keyfile=web_cfg.keyfile))
    else:
        logging.getLogger(__name__).warning("no TLS certificate (%s); phone page disabled - run tools/make_cert.sh",
                                            web_cfg.certfile)
    for c in servers:
        s = uvicorn.Server(c)
        threading.Thread(target=s.run, daemon=True, name=f"web-{c.port}").start()
