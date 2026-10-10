"""Minimal client for the AMap (高德) Web service REST API.

Endpoints used:
- /v3/direction/walking          步行路径规划
- /v3/assistant/coordinate/convert  GPS -> 高德坐标
- /v3/geocode/geo                地理编码（语音目的地不在预设里时）

The key comes from the AMAP_KEY environment variable. It is never logged:
URLs are redacted before they reach the log.

To be verified on site: whether walking routes cover Hong Kong, and what
fields mark a street crossing (CROSSING_WORDS below is a first guess based on
instruction text).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)

BASE = "https://restapi.amap.com"
CROSSING_WORDS = ("人行横道", "斑马线", "过马路", "穿过马路", "过街", "天桥", "地下通道")

# order matters: more specific first
ACTION_MAP = (
    ("左转调头", "turn_around"),
    ("掉头", "turn_around"),
    ("调头", "turn_around"),
    ("左前方", "slight_left"),
    ("右前方", "slight_right"),
    ("左后方", "turn_left"),
    ("右后方", "turn_right"),
    ("左转", "turn_left"),
    ("右转", "turn_right"),
    ("直行", "go_straight"),
)


class AmapError(Exception):
    """AMap returned status != 1."""


class NetworkError(Exception):
    """No network / timeout. Navigation must exit; obstacle avoidance continues."""


Fetch = Callable[[str, float], bytes]


def _urllib_fetch(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def redact(url: str) -> str:
    return re.sub(r"(key=)[^&]+", r"\1***", url)


@dataclass
class Step:
    instruction: str
    distance_m: float
    polyline: list[tuple[float, float]]
    action: str = ""
    assistant_action: str = ""
    road: str = ""

    @property
    def maneuver(self) -> str | None:
        """Prompt id for the action at the END of this step."""
        text = self.action or ""
        for word, pid in ACTION_MAP:
            if word in text:
                return pid
        return None

    @property
    def is_crossing(self) -> bool:
        text = self.instruction + self.action + self.assistant_action
        return any(w in text for w in CROSSING_WORDS)


@dataclass
class Route:
    distance_m: float
    duration_s: float
    steps: list[Step] = field(default_factory=list)

    @property
    def polyline(self) -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        for s in self.steps:
            for p in s.polyline:
                if not pts or pts[-1] != p:
                    pts.append(p)
        return pts


def _parse_polyline(s: str) -> list[tuple[float, float]]:
    pts = []
    for pair in filter(None, (s or "").split(";")):
        lng, lat = pair.split(",")
        pts.append((float(lng), float(lat)))
    return pts


def _str(v) -> str:
    # AMap returns [] for empty string fields
    return v if isinstance(v, str) else ""


def parse_walking(data: dict) -> Route:
    if str(data.get("status")) != "1":
        raise AmapError(f"{data.get('info')} ({data.get('infocode')})")
    paths = data.get("route", {}).get("paths") or []
    if not paths:
        raise AmapError("no walking path")
    p = paths[0]
    steps = [
        Step(
            instruction=_str(s.get("instruction")),
            distance_m=float(s.get("distance") or 0),
            polyline=_parse_polyline(_str(s.get("polyline"))),
            action=_str(s.get("action")),
            assistant_action=_str(s.get("assistant_action")),
            road=_str(s.get("road")),
        )
        for s in p.get("steps", [])
    ]
    return Route(float(p.get("distance") or 0), float(p.get("duration") or 0), steps)


class AmapClient:
    def __init__(self, key: str, fetch: Fetch | None = None, timeout_s: float = 5.0) -> None:
        if not key:
            raise ValueError("AMAP_KEY is not set")
        self._key = key
        self._fetch = fetch or _urllib_fetch
        self.timeout_s = timeout_s

    def _get(self, path: str, params: dict) -> dict:
        q = urllib.parse.urlencode({**params, "key": self._key, "output": "JSON"})
        url = f"{BASE}{path}?{q}"
        try:
            raw = self._fetch(url, self.timeout_s)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log.warning("amap request failed: %s %s", redact(url), e)
            raise NetworkError(str(e)) from None
        try:
            return json.loads(raw)
        except ValueError as e:
            raise AmapError(f"bad JSON: {e}") from None

    @staticmethod
    def _ll(lng: float, lat: float) -> str:
        return f"{lng:.6f},{lat:.6f}"

    def walking_route(self, origin: tuple[float, float], dest: tuple[float, float]) -> Route:
        data = self._get("/v3/direction/walking", {"origin": self._ll(*origin), "destination": self._ll(*dest)})
        return parse_walking(data)

    def convert_gps(self, lng: float, lat: float) -> tuple[float, float]:
        data = self._get("/v3/assistant/coordinate/convert", {"locations": self._ll(lng, lat), "coordsys": "gps"})
        if str(data.get("status")) != "1":
            raise AmapError(str(data.get("info")))
        a, b = data["locations"].split(";")[0].split(",")
        return float(a), float(b)

    def geocode(self, address: str, city: str = "") -> tuple[float, float]:
        params = {"address": address}
        if city:
            params["city"] = city
        data = self._get("/v3/geocode/geo", params)
        if str(data.get("status")) != "1" or not data.get("geocodes"):
            raise AmapError(f"geocode failed for {address!r}")
        a, b = data["geocodes"][0]["location"].split(",")
        return float(a), float(b)
