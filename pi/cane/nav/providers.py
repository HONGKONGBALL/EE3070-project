"""Which map service plans the routes and finds the places.

AMap covers the mainland and answers in GCJ-02. openrouteservice (free key, OpenStreetMap data) and
Google cover Hong Kong and answer in WGS-84, the frame of the phone's own fixes. Everything else in
the cane only sees the client.
"""

from __future__ import annotations

from typing import Literal

from ..config import Config
from .amap import AmapClient, Fetch
from .google import GoogleClient
from .ors import OrsClient

PROVIDERS = ("amap", "ors", "google")
MapClient = AmapClient | OrsClient | GoogleClient


def map_client(cfg: Config, *, fetch: Fetch | None = None) -> MapClient | None:
    """The client of the configured service, or None while its key is not set."""
    # Names and route text follow the language of the navigation prompts.
    english = cfg.general.language == "en"
    if cfg.nav.provider == "ors":
        return OrsClient(cfg.secrets.ors_key, language="en" if english else "zh-cn") if cfg.secrets.ors_key else None
    if cfg.nav.provider == "google":
        if not cfg.secrets.google_maps_key:
            return None
        return GoogleClient(cfg.secrets.google_maps_key, language="en" if english else "zh-CN")
    if not cfg.secrets.amap_key:
        return None
    return AmapClient(cfg.secrets.amap_key, fetch=fetch) if fetch else AmapClient(cfg.secrets.amap_key)


def map_frame(client: object) -> Literal["gcj02", "wgs84"]:
    """The coordinate frame a client speaks. Clients that do not say are AMap-like."""
    return "wgs84" if getattr(client, "frame", "gcj02") == "wgs84" else "gcj02"
