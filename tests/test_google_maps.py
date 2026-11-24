"""The Google adapter: same Route and place shapes as AMap, without coordinate conversion."""

import io
import json
import urllib.error

import pytest

from cane.app_navigation import NavigationMixin
from cane.config import load_config
from cane.nav.amap import AmapClient, AmapError, NetworkError, WalkingRouteBlocked, WalkingRouteUnavailable
from cane.nav.google import PLACES_URL, ROUTES_URL, GoogleClient, MissingGoogleKey, parse_route, turn_action
from cane.nav.providers import map_client, map_frame
from cane.voice.pi_places import PiPlaceSearch

ORIGIN = (114.1728, 22.3364)


def line(*points):
    return {"geoJsonLinestring": {"type": "LineString", "coordinates": [list(point) for point in points]}}


def step(points, metres, maneuver=None, text="", mode="WALK"):
    instruction = {"instructions": text, **({"maneuver": maneuver} if maneuver else {})}
    return {"distanceMeters": metres, "polyline": line(*points), "navigationInstruction": instruction,
            "travelMode": mode}


# East 100 m, left onto a road north for 50 m, then east again for 30 m. The last step has no
# maneuver, as walking steps often do.
EAST, NORTH, LAST = ((114.1728, 22.3364), (114.17377, 22.3364)), ((114.17377, 22.3364), (114.17377, 22.33685)), \
    ((114.17377, 22.33685), (114.17406, 22.33685))
ROUTE = {"routes": [{"distanceMeters": 180, "duration": "151s", "legs": [{"steps": [
    step(EAST, 100, "DEPART", "向东出发"),
    step(NORTH, 50, "TURN_LEFT", "向左转，进入达之路"),
    step(LAST, 30, None, "向右转"),
]}]}]}


class Recorder:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, json.loads(body), dict(headers)))
        if isinstance(self.reply, Exception):
            raise self.reply
        return json.dumps(self.reply).encode()


def test_each_stretch_takes_the_maneuver_of_the_step_after_it():
    route = parse_route(ROUTE)
    assert (route.distance_m, route.duration_s) == (180, 151)
    assert [s.maneuver for s in route.steps] == ["turn_left", "turn_right", None]
    assert [s.distance_m for s in route.steps] == [100, 50, 30]
    assert route.steps[0].polyline == [EAST[0], EAST[1]]
    assert route.polyline == [EAST[0], EAST[1], NORTH[1], LAST[1]]
    assert not any(s.is_crossing for s in route.steps) and not route.blocks_continuous_walking


def test_missing_maneuver_is_read_off_the_geometry():
    east, north, south, west = [(0.0, 0.0), (0.001, 0.0)], [(0.001, 0.0), (0.001, 0.001)], \
        [(0.001, 0.0), (0.001, -0.001)], [(0.001, 0.0), (0.0, 0.00001)]
    assert turn_action(east, north) == "左转"
    assert turn_action(east, south) == "右转"
    assert turn_action(east, [(0.001, 0.0), (0.002, 0.0)]) == "直行"
    assert turn_action(east, [(0.001, 0.0), (0.002, 0.0007)]) == "向左前方行走"
    assert turn_action(east, [(0.001, 0.0), (0.002, -0.0007)]) == "向右前方行走"
    assert turn_action(east, west) == "掉头"
    assert turn_action(east, [(0.001, 0.0)]) == "直行"


def test_steps_without_length_are_left_out():
    data = {"routes": [{"distanceMeters": 100, "duration": "80s", "legs": [{"steps": [
        step((EAST[0],), 0, "DEPART"), step(EAST, 100, "TURN_RIGHT")]}]}]}
    route = parse_route(data)
    assert len(route.steps) == 1 and route.steps[0].maneuver is None


@pytest.mark.parametrize("data", [{}, {"routes": []}])
def test_no_route_is_reported_as_unavailable(data):
    with pytest.raises(WalkingRouteUnavailable):
        parse_route(data)


@pytest.mark.parametrize("bad", [step(EAST, 100, "FERRY"), step(EAST, 100, "DEPART", mode="TRANSIT")])
def test_route_that_is_not_on_foot_is_refused(bad):
    with pytest.raises(WalkingRouteBlocked):
        parse_route({"routes": [{"legs": [{"steps": [bad]}]}]})


@pytest.mark.parametrize("data", [
    {"routes": [{"legs": [{"steps": []}]}]},
    {"routes": [{"legs": [{"steps": [{"distanceMeters": 5, "polyline": line((200.0, 22.0), (114.0, 22.0))}]}]}]},
    {"routes": [{"legs": [{"steps": [{"distanceMeters": 5, "polyline": {"encodedPolyline": "abc"}}]}]}]},
])
def test_unusable_geometry_is_refused(data):
    with pytest.raises(AmapError):
        parse_route(data)


def test_route_request_is_a_walk_in_chinese_with_the_key_in_a_header():
    post = Recorder(ROUTE)
    route = GoogleClient("secret", post).walking_route(ORIGIN, (114.1804, 22.3343))
    assert len(route.steps) == 3
    url, body, headers = post.calls[0]
    assert url == ROUTES_URL and "secret" not in url
    assert headers["X-Goog-Api-Key"] == "secret" and "routes.legs.steps.polyline" in headers["X-Goog-FieldMask"]
    assert body["travelMode"] == "WALK" and body["languageCode"] == "zh-CN"
    assert body["polylineEncoding"] == "GEO_JSON_LINESTRING"
    assert body["origin"] == {"location": {"latLng": {"latitude": 22.3364, "longitude": 114.1728}}}
    assert body["destination"] == {"location": {"latLng": {"latitude": 22.3343, "longitude": 114.1804}}}


PLACES = {"places": [
    {"id": "a", "displayName": {"text": "九龙塘站"}, "formattedAddress": "香港九龙塘",
     "location": {"latitude": 22.3369, "longitude": 114.1761}},
    {"id": "b", "displayName": {"text": "乐富站"}, "location": {"latitude": 22.338, "longitude": 114.187}},
    {"id": "", "displayName": {"text": "无编号"}, "location": {"latitude": 22.0, "longitude": 114.0}},
    {"id": "c", "displayName": {"text": "坐标不对"}, "location": {"latitude": 222.0, "longitude": 114.0}},
]}


def test_places_come_back_in_the_shape_of_amap_places():
    post = Recorder(PLACES)
    places = GoogleClient("secret", post).search_places("地铁站", ORIGIN)
    assert [(p.id, p.name, p.address, p.lng, p.lat) for p in places] == [
        ("a", "九龙塘站", "香港九龙塘", 114.1761, 22.3369), ("b", "乐富站", "", 114.187, 22.338)]
    url, body, headers = post.calls[0]
    assert url == PLACES_URL and headers["X-Goog-FieldMask"].startswith("places.id")
    assert body["textQuery"] == "地铁站" and body["rankPreference"] == "DISTANCE"
    assert body["locationBias"]["circle"]["center"] == {"latitude": 22.3364, "longitude": 114.1728}
    # Without a position the service ranks by relevance alone.
    GoogleClient("secret", post).search_places("香港城市大学")
    assert "locationBias" not in post.calls[1][1] and "rankPreference" not in post.calls[1][1]
    assert GoogleClient("secret", Recorder({})).search_places("没有") == ()
    assert GoogleClient("secret", Recorder(PLACES)).geocode("九龙塘站") == (114.1761, 22.3369)


def test_refusal_and_lost_network_are_told_apart():
    refused = urllib.error.HTTPError(ROUTES_URL, 403, "Forbidden", None, io.BytesIO(
        json.dumps({"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "API not enabled"}}).encode()))
    with pytest.raises(AmapError, match="HTTP 403 PERMISSION_DENIED"):
        GoogleClient("secret", Recorder(refused)).walking_route(ORIGIN, ORIGIN)
    with pytest.raises(NetworkError):
        GoogleClient("secret", Recorder(urllib.error.URLError("unreachable"))).walking_route(ORIGIN, ORIGIN)
    with pytest.raises(NetworkError):
        GoogleClient("secret", Recorder(TimeoutError())).search_places("地铁站")
    with pytest.raises(MissingGoogleKey):
        GoogleClient("")


def test_configuration_picks_the_service():
    cfg = load_config(None, {"AMAP_KEY": "a", "GOOGLE_MAPS_KEY": "g"})
    assert isinstance(map_client(cfg), AmapClient) and map_frame(map_client(cfg)) == "gcj02"
    cfg.nav.provider = "google"
    assert isinstance(map_client(cfg), GoogleClient) and map_frame(map_client(cfg)) == "wgs84"
    assert "g" not in repr(cfg.secrets).replace("google", "")
    # No key for the chosen service: no client, whatever other key is set.
    only_amap = load_config(None, {"AMAP_KEY": "a"})
    only_amap.nav.provider = "google"
    assert map_client(only_amap) is None
    assert map_frame(None) == "gcj02"


def test_unknown_service_is_refused(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[nav]\nprovider = "baidu"\n', "utf-8")
    with pytest.raises(ValueError, match="nav.provider"):
        load_config(path, {})


class Cane(NavigationMixin):
    """Just enough of the app for the coordinate conversion."""

    def __init__(self, amap, mode):
        self.cfg = load_config(None, {})
        self.cfg.nav.coord_mode = mode
        self.amap, self._coord_offset = amap, (0.005, -0.003)


@pytest.mark.parametrize("mode", ["offline_gcj02", "amap_api", "none"])
def test_fixes_are_not_shifted_for_a_service_that_speaks_wgs84(mode):
    cane = Cane(GoogleClient("secret", Recorder({})), mode)
    assert cane._to_amap(*ORIGIN) == ORIGIN and cane._to_amap_fast(*ORIGIN) == ORIGIN
    # AMap keeps its conversion.
    shifted = Cane(AmapClient("a", lambda url, timeout: b"{}"), "offline_gcj02")._to_amap(*ORIGIN)
    assert abs(shifted[0] - ORIGIN[0]) > 0.003


def test_place_search_passes_coordinates_through_unshifted():
    post = Recorder(PLACES)
    places = PiPlaceSearch(GoogleClient("secret", post)).search("地铁站", ORIGIN)
    assert post.calls[0][1]["locationBias"]["circle"]["center"] == {"latitude": 22.3, "longitude": 114.2}
    assert [(p.name, p.lng, p.lat, p.coordinate_frame) for p in places] == [
        ("九龙塘站", 114.1761, 22.3369, "WGS84"), ("乐富站", 114.187, 22.338, "WGS84")]
