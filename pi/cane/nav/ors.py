"""openrouteservice adapter: walking routes and place search on OpenStreetMap data.

For places AMap will not plan in, such as Hong Kong, where its key needs an abroad quota. The key
is free (openrouteservice.org, no card) with a daily allowance. Coordinates are WGS-84, the frame
of the phone's own fixes, so nothing is converted.

A step here says what to do at its start ("turn left onto X") and then how far to walk. The
navigator wants the opposite, as AMap gives it: walk this stretch, then do that. So each stretch
takes the maneuver of the step after it.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Final, TypeAlias

from .amap import (AmapError, AmapPlace, JsonMap, JsonValue, MissingAmapKey, NetworkError, Route, Step,
                   WalkingRouteUnavailable, _mapping, _number, _sequence, _text)
from .google import _point, turn_action
from .hant import to_simplified, to_traditional

log = logging.getLogger(__name__)
BASE: Final = "https://api.openrouteservice.org"
# Instruction types of the service, in the words AMap uses for the same maneuvers.
ACTIONS: Final = {0: "左转", 1: "右转", 2: "向左后方行走", 3: "向右后方行走", 4: "向左前方行走", 5: "向右前方行走",
                  6: "直行", 9: "掉头", 12: "向左前方行走", 13: "向右前方行走"}
# "The nearest station" is a kind of place, not a name: those go to the category search. Written
# in Traditional characters, as queries are compared after conversion. The numbers are the
# service's own category ids (POST /pois {"request": "list"}).
CATEGORIES: Final = (
    (("地鐵站", "港鐵站", "地鐵", "港鐵", "鐵路站", "火車站", "車站", "mtr", "mtr站", "mtr station", "subway station",
      "metro station", "train station", "railway station", "station", "subway", "metro"), (610, 597)),
    (("巴士站", "公交站", "公交車站", "巴士總站", "公車站", "bus stop", "bus station", "bus terminus"), (588, 587)),
    (("醫院", "hospital"), (206,)), (("診所", "clinic"), (202,)),
    (("藥店", "藥房", "pharmacy", "drugstore", "chemist"), (208,)),
    (("便利店", "便利商店", "convenience store", "convenience shop"), (451,)),
    (("超市", "超級市場", "supermarket", "grocery store"), (518,)),
    (("餐廳", "飯店", "餐館", "restaurant"), (570,)), (("快餐店", "快餐", "fast food", "fast food restaurant"), (566,)),
    (("咖啡店", "咖啡廳", "咖啡館", "cafe", "café", "coffee shop"), (564,)),
    (("廁所", "洗手間", "衞生間", "公廁", "toilet", "toilets", "restroom", "washroom", "bathroom", "public toilet"), (179,)),
    (("銀行", "bank"), (192,)), (("取款機", "提款機", "atm", "cash machine"), (191,)),
    (("公園", "park"), (280,)), (("圖書館", "library"), (133,)),
    (("商場", "購物中心", "mall", "shopping mall", "shopping centre", "shopping center"), (492,)),
    (("警察局", "警署", "派出所", "police station"), (369,)), (("酒店", "旅館", "賓館", "hotel"), (108,)),
)
# Said around a kind of place without naming one.
_FILLER: Final = re.compile(
    r"^(?:最近的?|附近的?|離我最近的?|就近的?)+|(?:在哪裏?|在哪兒)$"
    r"|^(?:(?:the|a|an|my)\s+)?(?:nearest|closest|nearby|near-by)\s+|^(?:the|a|an)\s+"
    r"|\s+(?:near\s*by|near me|around here|close to me)$")
# "九龍塘 Kowloon Tong": the name is tagged in both languages. One of them is spoken.
_BILINGUAL: Final = re.compile(r"^(.*[\u3400-\u9fff）)])\s+([A-Za-z0-9][A-Za-z0-9 .,'’()&/-]*)$")
POI_RADIUS_M: Final = 2000  # the most the category search takes around a point
Send: TypeAlias = Callable[[str, bytes | None, Mapping[str, str], float], bytes]


class MissingOrsKey(MissingAmapKey):
    """The openrouteservice adapter needs its own key."""


def _urllib_send(url: str, body: bytes | None, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST" if body else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_001)


def _pair(value: JsonValue) -> tuple[float, float]:
    values = _sequence(value)
    if len(values) < 2:
        raise AmapError("invalid coordinate in provider response")
    return _point({"longitude": values[0], "latitude": values[1]})


def parse_route(data: JsonValue) -> Route:
    features = _sequence(_mapping(data).get("features")) if _mapping(data).get("features") is not None else []
    if not features:
        raise WalkingRouteUnavailable("no walking path returned")
    feature = _mapping(features[0])
    line = [_pair(pair) for pair in _sequence(_mapping(feature.get("geometry")).get("coordinates"))]
    properties = _mapping(feature.get("properties"))
    stretches: list[tuple[list[tuple[float, float]], float, str, int]] = []
    for segment in _sequence(properties.get("segments")):
        for value in _sequence(_mapping(segment).get("steps")):
            step = _mapping(value)
            ends = _sequence(step.get("way_points"))
            kind = step.get("type")
            if len(ends) != 2 or not all(type(end) is int for end in ends) or type(kind) is not int:
                raise AmapError("invalid step in provider response")
            first, last = int(ends[0]), int(ends[1])  # type: ignore[arg-type]
            if not 0 <= first <= last < len(line):
                raise AmapError("step outside the route geometry")
            stretches.append((line[first:last + 1], _number(step.get("distance")), _text(step.get("instruction")), kind))
    walked = [stretch for stretch in stretches if len(stretch[0]) >= 2]
    if not walked:
        raise AmapError("walking path has no usable geometry")
    steps: list[Step] = []
    for index, (points, distance, text, _) in enumerate(walked):
        action = ""
        if index + 1 < len(walked):
            following = walked[index + 1]
            action = ACTIONS.get(following[3]) or turn_action(points, following[0])
        steps.append(Step(text, distance, points, action))
    summary = _mapping(properties.get("summary") or {})
    return Route(_number(summary.get("distance")), _number(summary.get("duration")), steps)


def category_ids(query: str) -> tuple[int, ...]:
    """The categories a query asks for, or () when it names a particular place."""
    wanted = " ".join(to_traditional(query).casefold().split())
    for _ in range(3):  # "the nearest ... near me"
        wanted = _FILLER.sub("", wanted).strip()
    wanted = wanted.replace(" ", "") if re.search(r"[\u3400-\u9fff]", wanted) else wanted
    for words, ids in CATEGORIES:
        if wanted in words:
            return ids
    return ()


def spoken_name(name: str, english: bool = False) -> str:
    """The half of a two-language name that the cane's voice can read.

    For the Mandarin voice the Chinese half is given in Simplified characters.
    """
    match = _BILINGUAL.match(name.strip())
    if english:
        return (match[2] if match else name).strip()
    return to_simplified((match[1] if match else name).strip())


def name_variants(query: str) -> list[str]:
    """Spellings to look a name up under. The map's Chinese names are Traditional."""
    traditional = to_traditional(query)
    plain = re.sub(r"(?:地鐵|港鐵)(?=站)", "", traditional)  # 九龍塘地鐵站 is named 九龍塘站
    variants = [query, traditional, plain, re.sub(r"站$", "", plain),
                re.sub(r"(?i)\s+(?:mtr\s+)?station$", "", query)]
    return [text for text in dict.fromkeys(variant.strip() for variant in variants) if len(text) >= 2]


class OrsClient:
    """Same calls as AmapClient, in WGS-84."""

    frame: Final = "wgs84"
    # The category search reaches 2 km, so the search position is rounded to about 1 km, not 10.
    search_decimals: Final = 2

    def __init__(self, key: str, send: Send | None = None, timeout_s: float = 8.0, *,
                 language: str = "zh-cn", base_url: str = BASE) -> None:
        if not key:
            raise MissingOrsKey("ORS_KEY is not set")
        self._key: str = key
        self._send: Send = send or _urllib_send
        self.timeout_s: float = timeout_s
        self.language: str = language
        self.english: bool = language.casefold().startswith("en")
        self.retry_pause_s: float = 0.4
        self.base_url: str = base_url.rstrip("/")

    def _call(self, path: str, body: Mapping[str, JsonValue] | None = None) -> JsonMap:
        url = self.base_url + path
        headers = {"Authorization": self._key, "Accept": "application/json, application/geo+json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        shown = url.split("?")[0]  # the key travels in a header; the query holds what was asked for
        payload = json.dumps(body).encode() if body is not None else None
        try:
            try:
                raw = self._send(url, payload, headers, self.timeout_s)
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError):
                # The service now and then drops a connection. Once more before giving up.
                time.sleep(self.retry_pause_s)
                raw = self._send(url, payload, headers, self.timeout_s)
        except urllib.error.HTTPError as exc:
            code = ""
            try:
                error = _mapping(json.loads(exc.read(20_000))).get("error")
                code = str(_mapping(error).get("code", "")) if isinstance(error, dict) else _text(error)[:60]
            except (ValueError, AmapError, OSError):
                pass
            log.warning("openrouteservice request rejected: %s (HTTP %s %s)", shown, exc.code, code)
            # 2010: no routable point near a coordinate. 2004: the route is longer than allowed.
            if code in ("2009", "2010"):
                raise WalkingRouteUnavailable("no walking path between these points") from None
            raise AmapError(f"provider rejected request (HTTP {exc.code} {code})".replace(" )", ")")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("openrouteservice request failed: %s (%s)", shown, type(exc).__name__)
            raise NetworkError("openrouteservice request failed; check connection and provider availability") from None
        if len(raw) > 2_000_000:
            raise AmapError("provider response exceeds size limit")
        try:
            decoded: JsonValue = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise AmapError("provider response was not valid JSON") from exc
        return _mapping(decoded)

    def walking_route(self, origin: tuple[float, float], dest: tuple[float, float]) -> Route:
        data = self._call("/v2/directions/foot-walking/geojson", {
            "coordinates": [[round(origin[0], 7), round(origin[1], 7)], [round(dest[0], 7), round(dest[1], 7)]],
            "language": self.language, "instructions": True, "units": "m"})
        return parse_route(dict(data))

    def search_places(self, query: str, region: tuple[float, float] | None = None) -> tuple[AmapPlace, ...]:
        """Places for a spoken request: a kind of place nearby, or a place by its name."""
        query = query.strip()
        kinds = category_ids(query)
        if kinds and region is not None:
            places = self._nearby(kinds, region)
            if places:
                return places
        for text in name_variants(query):
            places = self._named(text, region)
            if places:
                return places
        return ()

    def _nearby(self, kinds: tuple[int, ...], region: tuple[float, float]) -> tuple[AmapPlace, ...]:
        data = self._call("/pois", {
            "request": "pois", "limit": 30, "sortby": "distance", "filters": {"category_ids": list(kinds)},
            "geometry": {"buffer": POI_RADIUS_M,
                         "geojson": {"type": "Point", "coordinates": [round(region[0], 6), round(region[1], 6)]}}})
        matches: dict[str, AmapPlace] = {}
        for entry in _sequence(data.get("features")) if data.get("features") is not None else []:
            feature = _mapping(entry)
            properties = _mapping(feature.get("properties") or {})
            name = spoken_name(_text(_mapping(properties.get("osm_tags") or {}).get("name")), self.english)
            if not name:
                continue  # a place without a name cannot be read out or told apart
            labels = [_text(_mapping(value).get("category_name"))
                      for value in _mapping(properties.get("category_ids") or {}).values()]
            try:
                lng_lat = _pair(_mapping(feature.get("geometry")).get("coordinates"))
            except AmapError:
                continue
            place_id = f"osm:{properties.get('osm_type')}:{properties.get('osm_id')}"
            matches[place_id] = AmapPlace(place_id, name, " ".join(label for label in labels if label), *lng_lat)
        return tuple(matches.values())

    def _named(self, query: str, region: tuple[float, float] | None) -> tuple[AmapPlace, ...]:
        params = {"text": query, "size": "20", "lang": self.language.split("-")[0]}
        if region is not None:
            lng, lat = f"{region[0]:.6f}", f"{region[1]:.6f}"
            params.update({"focus.point.lon": lng, "focus.point.lat": lat, "boundary.circle.lon": lng,
                           "boundary.circle.lat": lat, "boundary.circle.radius": "20"})
        data = self._call("/geocode/search?" + urllib.parse.urlencode(params))
        matches: dict[str, AmapPlace] = {}
        for entry in _sequence(data.get("features")) if data.get("features") is not None else []:
            feature = _mapping(entry)
            properties = _mapping(feature.get("properties") or {})
            place_id = _text(properties.get("gid")) or _text(properties.get("id"))
            name = _text(properties.get("name"))
            if not place_id or not name:
                continue
            try:
                lng_lat = _pair(_mapping(feature.get("geometry")).get("coordinates"))
            except AmapError:
                continue
            label = _text(properties.get("label"))
            matches[place_id] = AmapPlace(place_id, spoken_name(name, self.english),
                                          label if self.english else to_simplified(label), *lng_lat)
        return tuple(matches.values())

    def convert_gps(self, lng: float, lat: float) -> tuple[float, float]:
        return lng, lat

    def geocode(self, address: str, city: str = "") -> tuple[float, float]:
        places = self.search_places(f"{city} {address}".strip())
        if not places:
            raise AmapError("no geocode results")
        return places[0].lng, places[0].lat
