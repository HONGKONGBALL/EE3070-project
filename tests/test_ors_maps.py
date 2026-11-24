"""The openrouteservice adapter, against trimmed answers the service gave on 2026-11-24."""

import io
import json
import urllib.error
from pathlib import Path

import pytest

from cane.config import load_config
from cane.nav.amap import AmapError, NetworkError, WalkingRouteUnavailable
from cane.nav.hant import to_simplified, to_traditional
from cane.nav.ors import MissingOrsKey, OrsClient, category_ids, name_variants, parse_route, spoken_name
from cane.nav.providers import map_client, map_frame
from cane.voice.pi_places import PiPlaceSearch

RECORDED = json.loads((Path(__file__).parent / "data" / "ors_kowloon_tong.json").read_text("utf-8"))
CITYU = (114.1728, 22.3364)


class Service:
    """Answers by path, and keeps what it was asked."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, json.loads(body) if body else None, dict(headers)))
        for part, answer in self.answers.items():
            if part in url:
                if isinstance(answer, list):
                    answer = answer.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return json.dumps(answer).encode()
        raise AssertionError(url)


def test_real_route_becomes_stretches_that_end_in_the_next_maneuver():
    route = parse_route(RECORDED["route"])
    assert (round(route.distance_m), round(route.duration_s)) == (545, 392)
    # depart, left, straight on, keep left, right, right, goal
    assert [s.maneuver for s in route.steps] == ["turn_left", "go_straight", "slight_left", "turn_right",
                                                 "turn_right", None]
    assert [round(s.distance_m) for s in route.steps] == [94, 46, 181, 126, 81, 17]
    line = [tuple(point) for point in RECORDED["route"]["features"][0]["geometry"]["coordinates"]]
    assert route.polyline == line
    assert route.steps[0].polyline == line[0:2] and route.steps[-1].polyline == line[30:32]
    assert not route.blocks_continuous_walking and not any(s.is_crossing for s in route.steps)


def test_route_request_is_on_foot_with_the_key_in_a_header():
    service = Service(directions=RECORDED["route"])
    route = OrsClient("secret", service).walking_route(CITYU, (114.1761, 22.3369))
    assert len(route.steps) == 6
    url, body, headers = service.calls[0]
    assert url == "https://api.openrouteservice.org/v2/directions/foot-walking/geojson" and "secret" not in url
    assert headers["Authorization"] == "secret"
    assert body == {"coordinates": [[114.1728, 22.3364], [114.1761, 22.3369]], "language": "zh-cn",
                    "instructions": True, "units": "m"}


@pytest.mark.parametrize("data", [{}, {"features": []}])
def test_no_route_is_reported_as_unavailable(data):
    with pytest.raises(WalkingRouteUnavailable):
        parse_route(data)


def test_route_with_a_step_outside_its_geometry_is_refused():
    broken = json.loads(json.dumps(RECORDED["route"]))
    broken["features"][0]["properties"]["segments"][0]["steps"][2]["way_points"] = [3, 99]
    with pytest.raises(AmapError):
        parse_route(broken)


@pytest.mark.parametrize(("query", "ids"), [
    ("地铁站", (610, 597)), ("最近的地铁站", (610, 597)), ("附近的港铁站", (610, 597)), ("MTR station", (610, 597)),
    ("the nearest MTR station", (610, 597)), ("nearest subway station", (610, 597)),
    ("医院", (206,)), ("nearest hospital", (206,)), ("a hospital nearby", (206,)),
    ("便利店", (451,)), ("convenience store", (451,)), ("洗手间", (179,)), ("Toilet", (179,)),
    ("library near me", (133,)), ("图书馆", (133,)), ("巴士站", (588, 587)), ("bus stop", (588, 587)),
    # A particular place is looked up by its name.
    ("九龙塘地铁站", ()), ("Kowloon Tong station", ()), ("香港城市大学", ()), ("Festival Walk", ()),
    ("麦当劳", ()), ("玛丽医院", ()), ("Queen Mary Hospital", ()), ("", ()),
])
def test_kinds_of_place_are_told_from_names(query, ids):
    assert category_ids(query) == ids


def test_names_are_spoken_in_one_language():
    assert spoken_name("九龍塘 Kowloon Tong") == "九龙塘"
    assert spoken_name("九龍塘 Kowloon Tong", english=True) == "Kowloon Tong"
    assert spoken_name("九龍塘（又一城） Kowloon Tong (Festival Walk)") == "九龙塘（又一城）"
    assert spoken_name("香港浸信會醫院 Hong Kong Baptist Hospital") == "香港浸信会医院"
    assert spoken_name("港鐵觀塘綫") == "港铁观塘线"
    assert spoken_name("九龍塘（又一城） Kowloon Tong (Festival Walk)", english=True) == "Kowloon Tong (Festival Walk)"
    assert spoken_name("759阿信屋 759 Store") == "759阿信屋"
    # One language only: nothing to choose.
    assert spoken_name("7-Eleven", english=True) == spoken_name("7-Eleven") == "7-Eleven"
    assert spoken_name("OK便利商店", english=True) == "OK便利商店"


def test_names_are_looked_up_in_traditional_characters():
    assert to_traditional("九龙塘地铁站 麦当劳 医院 图书馆 乐富 铜锣湾 炮台山") == "九龍塘地鐵站 麥當勞 醫院 圖書館 樂富 銅鑼灣 炮台山"
    assert to_simplified("九龍塘地鐵站 麥當勞 醫院 圖書館 樂富 銅鑼灣 炮台山") == "九龙塘地铁站 麦当劳 医院 图书馆 乐富 铜锣湾 炮台山"
    assert name_variants("九龙塘地铁站") == ["九龙塘地铁站", "九龍塘地鐵站", "九龍塘站", "九龍塘"]
    assert name_variants("乐富站") == ["乐富站", "樂富站", "樂富"]
    assert name_variants("Lok Fu MTR station") == ["Lok Fu MTR station", "Lok Fu"]
    assert name_variants("Festival Walk") == ["Festival Walk"]


def test_nearest_station_comes_from_the_category_search():
    service = Service(pois=RECORDED["stations"])
    places = OrsClient("secret", service).search_places("最近的地铁站", (114.17, 22.34))
    # Read by the Mandarin voice, so in Simplified characters.
    assert [p.name for p in places] == ["白田", "九龙塘（又一城）", "九龙塘", "九龙塘", "石硖尾"]
    assert len({p.id for p in places}) == 5 and all(p.id.startswith("osm:") for p in places)
    assert all(114.1 < p.lng < 114.2 and 22.3 < p.lat < 22.4 for p in places)
    url, body, _ = service.calls[0]
    assert url.endswith("/pois") and body["filters"] == {"category_ids": [610, 597]}
    assert body["geometry"] == {"buffer": 2000, "geojson": {"type": "Point", "coordinates": [114.17, 22.34]}}
    english = OrsClient("secret", Service(pois=RECORDED["stations"]), language="en")
    assert [p.name for p in english.search_places("the nearest MTR station", (114.17, 22.34))][:3] == [
        "Pak Tin", "Kowloon Tong (Festival Walk)", "Kowloon Tong"]


def test_named_place_is_found_under_its_traditional_spelling():
    nothing = {"type": "FeatureCollection", "features": []}
    service = Service(geocode=[nothing, nothing, RECORDED["named"]])
    places = OrsClient("secret", service).search_places("九龙塘地铁站", (114.17, 22.34))
    assert [(p.name, p.id, p.address) for p in places] == [
        ("九龙塘站", "openstreetmap:venue:way/186845719", "九龙塘站, 香港, CW, 香港")]
    asked = [call[0].split("text=")[1].split("&")[0] for call in service.calls]
    assert len(asked) == 3 and all("secret" not in call[0] for call in service.calls)
    assert "focus.point.lon=114.170000" in service.calls[0][0] and "boundary.circle.radius=20" in service.calls[0][0]


def test_kind_without_a_position_or_without_results_falls_back_to_the_name_search():
    nothing = {"type": "FeatureCollection", "features": []}
    service = Service(pois=nothing, geocode=RECORDED["named"])
    assert len(OrsClient("secret", service).search_places("地铁站", (114.17, 22.34))) == 1
    assert [call[0].split("/")[3].split("?")[0] for call in service.calls] == ["pois", "geocode"]
    service = Service(geocode=RECORDED["named"])
    assert len(OrsClient("secret", service).search_places("地铁站")) == 1
    assert "focus.point" not in service.calls[0][0]


def test_dropped_connection_is_tried_once_more():
    service = Service(directions=[ConnectionResetError(), RECORDED["route"]])
    client = OrsClient("secret", service)
    client.retry_pause_s = 0
    assert len(client.walking_route(CITYU, (114.1761, 22.3369)).steps) == 6
    assert len(service.calls) == 2
    client = OrsClient("secret", Service(directions=[TimeoutError(), urllib.error.URLError("down")]))
    client.retry_pause_s = 0
    with pytest.raises(NetworkError):
        client.walking_route(CITYU, CITYU)


def test_refusals_are_told_apart():
    def refusal(status, code):
        body = json.dumps({"error": {"code": code, "message": "no"}}).encode()
        return urllib.error.HTTPError("https://api.openrouteservice.org/x", status, "no", None, io.BytesIO(body))

    with pytest.raises(WalkingRouteUnavailable):
        OrsClient("secret", Service(directions=refusal(404, 2010))).walking_route(CITYU, CITYU)
    with pytest.raises(AmapError, match="HTTP 403"):
        OrsClient("secret", Service(directions=refusal(403, 0))).walking_route(CITYU, CITYU)
    quota = urllib.error.HTTPError("https://api.openrouteservice.org/x", 429, "no", None,
                                   io.BytesIO(b'{"error": "Rate limit exceeded"}'))
    with pytest.raises(AmapError, match="HTTP 429"):
        OrsClient("secret", Service(geocode=quota)).search_places("九龍塘")
    with pytest.raises(MissingOrsKey):
        OrsClient("")


def test_configuration_picks_the_service_and_its_language():
    cfg = load_config(None, {"AMAP_KEY": "a", "ORS_KEY": "o"})
    cfg.nav.provider = "ors"
    client = map_client(cfg)
    assert isinstance(client, OrsClient) and map_frame(client) == "wgs84" and not client.english
    cfg.general.language = "en"
    assert map_client(cfg).english and map_client(cfg).language == "en"
    assert "ors_key=set" in repr(cfg.secrets) and "'o'" not in repr(cfg.secrets)
    missing = load_config(None, {"AMAP_KEY": "a"})
    missing.nav.provider = "ors"
    assert map_client(missing) is None


def test_cane_place_search_uses_a_nearer_anchor_and_no_coordinate_shift():
    service = Service(pois=RECORDED["stations"])
    places = PiPlaceSearch(OrsClient("secret", service)).search("地铁站", CITYU)
    # Rounded to about a kilometre, so that the 2 km category search covers the user.
    assert service.calls[0][1]["geometry"]["geojson"]["coordinates"] == [114.17, 22.34]
    recorded = RECORDED["stations"]["features"][2]["geometry"]["coordinates"]
    assert (places[2].name, places[2].lng, places[2].lat, places[2].coordinate_frame) == (
        "九龙塘", recorded[0], recorded[1], "WGS84")
