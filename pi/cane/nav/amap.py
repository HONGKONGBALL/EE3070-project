"""AMap REST adapter, with strict response parsing and redacted failures.

The production host remains AMap. A base_url override allows real HTTP fixture
coverage without production keys; no live coverage or Hong Kong mapping is
claimed by those fixture tests.
"""

from __future__ import annotations

import json
import logging
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Final, Protocol, TypeAlias, assert_never

log = logging.getLogger(__name__)
BASE: Final = "https://restapi.amap.com"
CROSSING_WORDS: Final = ("人行横道", "斑马线", "过马路", "穿过马路", "过街", "天桥", "地下通道")
# AMap's documented walking segment types for cableway, boat, sightseeing vehicle,
# slide and ferry respectively.
NON_WALKING_SEGMENT_TYPES: Final = frozenset({"10", "14", "15", "16", "30"})
PEDESTRIAN_RESTRICTIONS: Final = frozenset({
    "禁止行人通行", "禁止步行", "行人禁止通行", "禁止行人进入", "道路封闭", "路段封闭",
    "禁止行人進入", "道路封閉", "路段封閉", "no pedestrian access", "closed to pedestrians",
})
ACTION_MAP: Final = (
    ("左转调头", "turn_around"), ("掉头", "turn_around"), ("调头", "turn_around"),
    ("左前方", "slight_left"), ("右前方", "slight_right"), ("左后方", "turn_left"),
    ("右后方", "turn_right"), ("左转", "turn_left"), ("右转", "turn_right"), ("直行", "go_straight"),
)
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonMap: TypeAlias = Mapping[str, JsonValue]
Fetch: TypeAlias = Callable[[str, float], bytes]


class AmapError(Exception):
    """The provider response cannot be used as a walking route."""


class WalkingRouteUnavailable(AmapError):
    """The provider returned no walking paths for these endpoints."""


class WalkingRouteBlocked(AmapError):
    """A route has an explicit restriction or requires non-walking travel."""


class NetworkError(Exception):
    """The provider is unreachable; obstacle avoidance must remain independent."""


class MissingAmapKey(ValueError):
    """A real map adapter requires an explicit API key."""


@dataclass(frozen=True, slots=True)
class Step:
    instruction: str
    distance_m: float
    polyline: list[tuple[float, float]]
    action: str = ""
    assistant_action: str = ""
    road: str = ""
    walk_type: str = ""

    @property
    def maneuver(self) -> str | None:
        for word, prompt_id in ACTION_MAP:
            if word in self.action:
                return prompt_id
        return None

    @property
    def is_crossing(self) -> bool:
        return any(word in self.instruction + self.action + self.assistant_action for word in CROSSING_WORDS)

    @property
    def blocks_continuous_walking(self) -> bool:
        if self.walk_type in NON_WALKING_SEGMENT_TYPES:
            return True
        return any(
            text.strip(" \t\n。.!！").casefold() in PEDESTRIAN_RESTRICTIONS
            for text in (self.instruction, self.action, self.assistant_action)
        )


@dataclass(frozen=True, slots=True)
class Route:
    distance_m: float
    duration_s: float
    steps: list[Step] = field(default_factory=list)
    uses_alternative: bool = False

    @property
    def blocks_continuous_walking(self) -> bool:
        return any(step.blocks_continuous_walking for step in self.steps)

    @property
    def polyline(self) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for step in self.steps:
            for point in step.polyline:
                if not points or points[-1] != point:
                    points.append(point)
        return points


class RouteClient(Protocol):
    """Shared contract for the real provider and the explicitly synthetic demo."""

    def walking_route(self, origin: tuple[float, float], dest: tuple[float, float]) -> Route: ...
    def convert_gps(self, lng: float, lat: float) -> tuple[float, float]: ...
    def geocode(self, address: str, city: str = "") -> tuple[float, float]: ...


def _mapping(value: JsonValue) -> JsonMap:
    match value:
        case dict() as mapping:
            return mapping
        case None | bool() | int() | float() | str() | list():
            raise AmapError("expected a JSON object in provider response")
        case unreachable:
            assert_never(unreachable)


def _sequence(value: JsonValue) -> list[JsonValue]:
    match value:
        case list() as values:
            return values
        case None | bool() | int() | float() | str() | dict():
            raise AmapError("expected a JSON array in provider response")
        case unreachable:
            assert_never(unreachable)


def _text(value: JsonValue) -> str:
    match value:
        case str() as text:
            return text
        case None | bool() | int() | float() | list() | dict():
            return ""  # AMap uses [] for empty textual fields.
        case unreachable:
            assert_never(unreachable)


def _number(value: JsonValue) -> float:
    match value:
        case bool():
            raise AmapError("boolean is not a valid route distance/duration")
        case str() | int() | float():
            try:
                number = float(value)
            except ValueError as exc:
                raise AmapError("invalid numeric value in provider response") from exc
            if math.isfinite(number) and number >= 0:
                return number
            raise AmapError("non-finite or negative distance/duration in provider response")
        case None:
            return 0.0
        case list() | dict():
            raise AmapError("invalid numeric shape in provider response")
        case unreachable:
            assert_never(unreachable)


def _location(value: str) -> tuple[float, float]:
    try:
        lng, lat = (float(component) for component in value.split(","))
    except (ValueError, TypeError) as exc:
        raise AmapError("invalid coordinate in provider response") from exc
    if not (math.isfinite(lng) and math.isfinite(lat) and -180 <= lng <= 180 and -90 <= lat <= 90):
        raise AmapError("coordinate outside valid geographic range")
    return lng, lat


def _parse_walking_path(path: JsonMap) -> Route:
    steps: list[Step] = []
    for value in _sequence(path.get("steps")):
        step = _mapping(value)
        points = [_location(pair) for pair in _text(step.get("polyline")).split(";") if pair]
        walk_type = step.get("walk_type")
        steps.append(Step(_text(step.get("instruction")), _number(step.get("distance")), points,
                          _text(step.get("action")), _text(step.get("assistant_action")), _text(step.get("road")),
                          str(walk_type) if type(walk_type) is int else _text(walk_type)))
    route = Route(_number(path.get("distance")), _number(path.get("duration")), steps)
    if not route.blocks_continuous_walking and not any(step.polyline for step in steps):
        raise AmapError("walking path has no usable geometry")
    return route


def parse_walking(data: JsonValue) -> Route:
    payload = _mapping(data)
    status, code = str(payload.get("status")), str(payload.get("infocode"))
    if status != "1":
        if status == "0" and code == "20801":
            raise WalkingRouteUnavailable("no roads near the requested planning points")
        raise AmapError(f"provider rejected request ({_text(payload.get('infocode'))})")
    route_data = payload.get("route")
    if route_data is None and str(payload.get("count")) == "0":
        raise WalkingRouteUnavailable("no walking path returned")
    paths = _sequence(_mapping(route_data).get("paths"))
    if not paths:
        raise WalkingRouteUnavailable("no walking path returned")
    blocked = False
    invalid: AmapError | None = None
    for path in paths:
        try:
            route = _parse_walking_path(_mapping(path))
        except AmapError as error:
            invalid = error
            continue
        if route.blocks_continuous_walking:
            blocked = True
            continue
        return replace(route, uses_alternative=True) if blocked else route
    if invalid is not None:
        raise invalid
    raise WalkingRouteBlocked("all supplied paths contain a pedestrian restriction or non-walking segment")


def _urllib_fetch(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read(2_000_001)


def redact(url: str) -> str:
    return re.sub(r"(key=)[^&\s]+", r"\1***", url)


class AmapClient:
    def __init__(self, key: str, fetch: Fetch | None = None, timeout_s: float = 5.0, *, base_url: str = BASE) -> None:
        if not key:
            raise MissingAmapKey("AMAP_KEY is not set")
        self._key: str = key
        self._fetch: Fetch = fetch or _urllib_fetch
        self.timeout_s: float = timeout_s
        self.base_url: str = base_url.rstrip("/")

    def _get(self, path: str, params: Mapping[str, str]) -> JsonMap:
        query = urllib.parse.urlencode({**params, "key": self._key, "output": "JSON"})
        url = f"{self.base_url}{path}?{query}"
        try:
            raw = self._fetch(url, self.timeout_s)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Error messages can themselves contain the complete access-bearing URL.
            log.warning("amap request failed: %s (%s)", redact(url), type(exc).__name__)
            raise NetworkError("AMap request failed; check connection and provider availability") from None
        if len(raw) > 2_000_000:
            raise AmapError("provider response exceeds size limit")
        try:
            decoded: JsonValue = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise AmapError("provider response was not valid JSON") from exc
        return _mapping(decoded)

    @staticmethod
    def _ll(lng: float, lat: float) -> str:
        return f"{lng:.6f},{lat:.6f}"

    def walking_route(self, origin: tuple[float, float], dest: tuple[float, float]) -> Route:
        data = self._get("/v3/direction/walking", {"origin": self._ll(*origin), "destination": self._ll(*dest)})
        return parse_walking(dict(data))

    def convert_gps(self, lng: float, lat: float) -> tuple[float, float]:
        data = self._get("/v3/assistant/coordinate/convert", {"locations": self._ll(lng, lat), "coordsys": "gps"})
        if str(data.get("status")) != "1":
            raise AmapError("coordinate conversion rejected by provider")
        return _location(_text(data.get("locations")).split(";")[0])

    def geocode(self, address: str, city: str = "") -> tuple[float, float]:
        params = {"address": address, **({"city": city} if city else {})}
        data = self._get("/v3/geocode/geo", params)
        if str(data.get("status")) != "1":
            raise AmapError("geocode rejected by provider")
        places = _sequence(data.get("geocodes"))
        if not places:
            raise AmapError("no geocode results")
        return _location(_text(_mapping(places[0]).get("location")))
