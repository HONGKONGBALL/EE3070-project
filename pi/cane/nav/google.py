"""Google Maps Platform adapter: walking routes (Routes API) and place search (Places API).

For places AMap will not plan in, such as Hong Kong, where its key needs an abroad quota. Google
answers in WGS-84 there, the frame of the phone's own fixes, so nothing is converted. It has no
walking routes in the mainland.

A Google step says what to do at its start ("turn left onto X") and then how far to walk. The
navigator wants the opposite, as AMap gives it: walk this stretch, then do that. So each stretch
takes the maneuver of the step after it.
"""

from __future__ import annotations

import json
import logging
import math
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Final, TypeAlias

from .amap import (AmapError, AmapPlace, JsonMap, JsonValue, MissingAmapKey, NetworkError, Route, Step,
                   WalkingRouteBlocked, WalkingRouteUnavailable, _mapping, _number, _sequence, _text)
from .coords import bearing_deg, haversine_m

log = logging.getLogger(__name__)
ROUTES_URL: Final = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_URL: Final = "https://places.googleapis.com/v1/places:searchText"
ROUTE_FIELDS: Final = ("routes.distanceMeters,routes.duration,routes.legs.steps.distanceMeters,"
                       "routes.legs.steps.polyline,routes.legs.steps.navigationInstruction,"
                       "routes.legs.steps.travelMode")
PLACE_FIELDS: Final = "places.id,places.displayName,places.formattedAddress,places.location"
# The words AMap uses for the same maneuvers: Step.maneuver reads them.
ACTIONS: Final = {
    "TURN_LEFT": "左转", "TURN_RIGHT": "右转",
    "TURN_SLIGHT_LEFT": "向左前方行走", "TURN_SLIGHT_RIGHT": "向右前方行走",
    "TURN_SHARP_LEFT": "向左后方行走", "TURN_SHARP_RIGHT": "向右后方行走",
    "UTURN_LEFT": "掉头", "UTURN_RIGHT": "掉头",
    "FORK_LEFT": "向左前方行走", "FORK_RIGHT": "向右前方行走",
    "RAMP_LEFT": "向左前方行走", "RAMP_RIGHT": "向右前方行走",
    "ROUNDABOUT_LEFT": "向左前方行走", "ROUNDABOUT_RIGHT": "向右前方行走",
    "STRAIGHT": "直行", "NAME_CHANGE": "直行", "MERGE": "直行",
}
NOT_ON_FOOT: Final = frozenset({"FERRY", "FERRY_TRAIN"})
Post: TypeAlias = Callable[[str, bytes, Mapping[str, str], float], bytes]


class MissingGoogleKey(MissingAmapKey):
    """The Google adapter needs its own key."""


def _urllib_post(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_001)


def _point(value: JsonValue) -> tuple[float, float]:
    """(lng, lat) from a Google {"latitude": .., "longitude": ..} object."""
    location = _mapping(value)
    lat, lng = location.get("latitude"), location.get("longitude")
    if isinstance(lat, bool) or isinstance(lng, bool) or not isinstance(lat, (int, float)) \
            or not isinstance(lng, (int, float)):
        raise AmapError("invalid coordinate in provider response")
    if not (math.isfinite(lng) and math.isfinite(lat) and -180 <= lng <= 180 and -90 <= lat <= 90):
        raise AmapError("coordinate outside valid geographic range")
    return float(lng), float(lat)


def _line(polyline: JsonValue) -> list[tuple[float, float]]:
    """The points of a step, asked for as a GeoJSON LineString of [lng, lat] pairs."""
    points: list[tuple[float, float]] = []
    for pair in _sequence(_mapping(_mapping(polyline).get("geoJsonLinestring")).get("coordinates")):
        values = _sequence(pair)
        if len(values) < 2:
            raise AmapError("invalid coordinate in provider response")
        points.append(_point({"longitude": values[0], "latitude": values[1]}))
    return points


def turn_action(before: list[tuple[float, float]], after: list[tuple[float, float]]) -> str:
    """The maneuver between two stretches, read off their geometry.

    Used when a step comes without a maneuver, which walking steps often do.
    """
    def heading(points: list[tuple[float, float]], *, at_end: bool) -> float | None:
        ordered = points[::-1] if at_end else points
        anchor = ordered[0]
        for other in ordered[1:]:
            if haversine_m(*anchor, *other) >= 3.0:
                return bearing_deg(*other, *anchor) if at_end else bearing_deg(*anchor, *other)
        return None

    arriving, leaving = heading(before, at_end=True), heading(after, at_end=False)
    if arriving is None or leaving is None:
        return "直行"
    turn = (leaving - arriving + 180) % 360 - 180
    side = "右" if turn > 0 else "左"
    if abs(turn) < 25:
        return "直行"
    if abs(turn) < 55:
        return f"向{side}前方行走"
    if abs(turn) <= 150:
        return f"{side}转"
    return "掉头"


def parse_route(data: JsonValue) -> Route:
    payload = _mapping(data)
    routes = _sequence(payload.get("routes")) if payload.get("routes") is not None else []
    if not routes:
        raise WalkingRouteUnavailable("no walking path returned")
    route = _mapping(routes[0])
    stretches: list[tuple[list[tuple[float, float]], float, str, str]] = []
    for leg in _sequence(route.get("legs")):
        for value in _sequence(_mapping(leg).get("steps")):
            step = _mapping(value)
            instruction: JsonMap = _mapping(step.get("navigationInstruction") or {})
            maneuver = _text(instruction.get("maneuver"))
            if maneuver in NOT_ON_FOOT or _text(step.get("travelMode")) not in ("", "WALK"):
                raise WalkingRouteBlocked("the route needs travel that is not on foot")
            points = _line(step.get("polyline"))
            if len(points) < 2:
                continue  # a step without length carries no walking
            stretches.append((points, _number(step.get("distanceMeters")),
                              _text(instruction.get("instructions")), maneuver))
    if not stretches:
        raise AmapError("walking path has no usable geometry")
    steps: list[Step] = []
    for index, (points, distance, text, _) in enumerate(stretches):
        action = ""
        if index + 1 < len(stretches):
            following = stretches[index + 1]
            action = ACTIONS.get(following[3]) or turn_action(points, following[0])
        steps.append(Step(text, distance, points, action))
    duration = _text(route.get("duration")).rstrip("s")
    return Route(_number(route.get("distanceMeters")), _number(duration or 0), steps)


class GoogleClient:
    """Same calls as AmapClient, in WGS-84."""

    frame: Final = "wgs84"

    def __init__(self, key: str, post: Post | None = None, timeout_s: float = 6.0, *,
                 language: str = "zh-CN") -> None:
        if not key:
            raise MissingGoogleKey("GOOGLE_MAPS_KEY is not set")
        self._key: str = key
        self._post: Post = post or _urllib_post
        self.timeout_s: float = timeout_s
        self.language: str = language

    def _call(self, url: str, body: Mapping[str, JsonValue], fields: str) -> JsonMap:
        headers = {"Content-Type": "application/json", "X-Goog-Api-Key": self._key, "X-Goog-FieldMask": fields}
        try:
            raw = self._post(url, json.dumps(body).encode(), headers, self.timeout_s)
        except urllib.error.HTTPError as exc:
            # The key travels in a header, so the URL and this reason are safe to log.
            reason = ""
            try:
                reason = _text(_mapping(_mapping(json.loads(exc.read(20_000))).get("error")).get("status"))
            except (ValueError, AmapError, OSError):
                pass
            log.warning("google request rejected: %s (HTTP %s %s)", url, exc.code, reason)
            raise AmapError(f"provider rejected request (HTTP {exc.code} {reason})".rstrip(" )") + ")") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("google request failed: %s (%s)", url, type(exc).__name__)
            raise NetworkError("Google request failed; check connection and provider availability") from None
        if len(raw) > 2_000_000:
            raise AmapError("provider response exceeds size limit")
        try:
            decoded: JsonValue = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise AmapError("provider response was not valid JSON") from exc
        return _mapping(decoded)

    @staticmethod
    def _waypoint(lng: float, lat: float) -> dict[str, JsonValue]:
        return {"location": {"latLng": {"latitude": round(lat, 7), "longitude": round(lng, 7)}}}

    def walking_route(self, origin: tuple[float, float], dest: tuple[float, float]) -> Route:
        data = self._call(ROUTES_URL, {
            "origin": self._waypoint(*origin), "destination": self._waypoint(*dest),
            "travelMode": "WALK", "languageCode": self.language, "units": "METRIC",
            "polylineEncoding": "GEO_JSON_LINESTRING"}, ROUTE_FIELDS)
        return parse_route(dict(data))

    def search_places(self, query: str, region: tuple[float, float] | None = None) -> tuple[AmapPlace, ...]:
        body: dict[str, JsonValue] = {"textQuery": query, "languageCode": self.language, "maxResultCount": 20}
        if region is not None:
            body["locationBias"] = {"circle": {"center": {"latitude": round(region[1], 7),
                                                         "longitude": round(region[0], 7)}, "radius": 20000.0}}
            body["rankPreference"] = "DISTANCE"
        data = self._call(PLACES_URL, body, PLACE_FIELDS)
        matches: dict[str, AmapPlace] = {}
        for entry in _sequence(data.get("places")) if data.get("places") is not None else []:
            place = _mapping(entry)
            place_id = _text(place.get("id"))
            name = _text(_mapping(place.get("displayName") or {}).get("text"))
            if not place_id or not name:
                continue
            try:
                lng, lat = _point(place.get("location"))
            except AmapError:
                continue
            matches[place_id] = AmapPlace(place_id, name, _text(place.get("formattedAddress")), lng, lat)
        return tuple(matches.values())

    def convert_gps(self, lng: float, lat: float) -> tuple[float, float]:
        return lng, lat

    def geocode(self, address: str, city: str = "") -> tuple[float, float]:
        places = self.search_places(f"{city} {address}".strip())
        if not places:
            raise AmapError("no geocode results")
        return places[0].lng, places[0].lat
