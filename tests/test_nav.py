import json
import urllib.error

import pytest

from cane.config import NavCfg
from cane.model import Direction
from cane.nav.amap import (
    AmapClient,
    AmapError,
    NetworkError,
    Step,
    WalkingRouteBlocked,
    WalkingRouteUnavailable,
    parse_walking,
    redact,
)
from cane.nav.coords import bearing_deg, haversine_m, wgs84_to_gcj02
from cane.nav.navigator import Navigator

# a small L-shaped route near CityU: 100 m north, turn right, cross a road, 60 m east
LNG0, LAT0 = 114.1700, 22.3360
DLAT_100M = 100 / 111195.0
DLNG_60M = 60 / (111195.0 * 0.9250)  # cos(22.336 deg)
A = (LNG0, LAT0)
B = (LNG0, LAT0 + DLAT_100M)
C = (LNG0 + DLNG_60M / 2, LAT0 + DLAT_100M)
D = (LNG0 + DLNG_60M, LAT0 + DLAT_100M)


def pl(*pts):
    return ";".join(f"{a:.6f},{b:.6f}" for a, b in pts)


ROUTE_JSON = {
    "status": "1",
    "info": "OK",
    "route": {
        "paths": [
            {
                "distance": "160",
                "duration": "128",
                "steps": [
                    {"instruction": "向北步行100米右转", "distance": "100", "polyline": pl(A, B), "action": "右转",
                     "assistant_action": [], "road": []},
                    {"instruction": "通过人行横道", "distance": "30", "polyline": pl(B, C), "action": [],
                     "assistant_action": "通过人行横道", "road": []},
                    {"instruction": "向东步行30米到达目的地", "distance": "30", "polyline": pl(C, D), "action": [],
                     "assistant_action": "到达目的地", "road": []},
                ],
            }
        ]
    },
}


def lerp(p, q, f):
    return (p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f)


def test_parse_walking():
    r = parse_walking(ROUTE_JSON)
    assert len(r.steps) == 3
    assert r.steps[0].maneuver == "turn_right"
    assert r.steps[1].is_crossing and not r.steps[0].is_crossing
    assert r.steps[2].maneuver is None
    assert r.polyline[0] == pytest.approx(A, abs=1e-6)


def test_parse_error():
    with pytest.raises(AmapError):
        parse_walking({"status": "0", "info": "INVALID_USER_KEY"})


def test_explicit_empty_walking_results_are_unavailable():
    with pytest.raises(WalkingRouteUnavailable):
        parse_walking({"status": "1", "count": "0", "route": {"paths": []}})


def test_ferry_only_path_is_not_a_walking_route():
    path = ROUTE_JSON["route"]["paths"][0]
    ferry = {**path, "steps": [{**path["steps"][0], "walk_type": "30"}]}
    with pytest.raises(WalkingRouteBlocked):
        parse_walking({"status": "1", "route": {"paths": [ferry]}})


def test_blocked_first_path_uses_provider_alternative():
    path = ROUTE_JSON["route"]["paths"][0]
    blocked = {**path, "steps": [{**path["steps"][0], "assistant_action": "禁止行人通行"}]}
    route = parse_walking({"status": "1", "route": {"paths": [blocked, path]}})
    assert route.uses_alternative and len(route.steps) == 3


def test_maneuver_mapping():
    assert Step("", 0, [], action="向左前方行走").maneuver == "slight_left"
    assert Step("", 0, [], action="左转调头").maneuver == "turn_around"
    assert Step("", 0, [], action="直行").maneuver == "go_straight"


def test_client_builds_url_and_redacts_key():
    seen = []

    def fetch(url, timeout):
        seen.append(url)
        return json.dumps(ROUTE_JSON).encode()

    c = AmapClient("SECRETKEY", fetch=fetch)
    r = c.walking_route(A, D)
    assert len(r.steps) == 3
    assert "/v3/direction/walking" in seen[0] and "key=SECRETKEY" in seen[0]
    assert "SECRETKEY" not in redact(seen[0])


def test_client_network_error():
    def fetch(url, timeout):
        raise urllib.error.URLError("no route to host")

    with pytest.raises(NetworkError):
        AmapClient("k", fetch=fetch).walking_route(A, D)


def test_client_requires_key():
    with pytest.raises(ValueError):
        AmapClient("")


def test_convert_and_geocode():
    def fetch(url, timeout):
        if "convert" in url:
            return b'{"status":"1","locations":"114.17,22.33"}'
        return b'{"status":"1","geocodes":[{"location":"114.1,22.3"}]}'

    c = AmapClient("k", fetch=fetch)
    assert c.convert_gps(1, 2) == (114.17, 22.33)
    assert c.geocode("x") == (114.1, 22.3)


def test_coords():
    assert haversine_m(*A, *B) == pytest.approx(100, abs=0.5)
    assert bearing_deg(*A, *B) == pytest.approx(0, abs=0.5)
    assert bearing_deg(*B, *D) == pytest.approx(90, abs=0.5)
    # GCJ-02 offset is a few hundred metres on the mainland, none outside China
    g = wgs84_to_gcj02(114.17, 22.33)
    assert 100 < haversine_m(114.17, 22.33, *g) < 800
    assert wgs84_to_gcj02(2.35, 48.85) == (2.35, 48.85)


@pytest.fixture
def nav():
    n = Navigator(NavCfg(announce_m=[50, 20, 8], arrive_m=8, offroute_m=25))
    out = n.start(parse_walking(ROUTE_JSON), D)
    assert [o.prompt_id for o in out] == ["nav_start"]
    return n


def test_turn_announced_once_per_threshold(nav):
    assert nav.update(*lerp(A, B, 0.1)) == []           # 90 m before the turn
    out = nav.update(*lerp(A, B, 0.55))                  # 45 m
    assert [(o.prompt_id, o.extra_clips, o.direction) for o in out] == [("turn_right", ["dist_50"], Direction.RIGHT)]
    assert nav.update(*lerp(A, B, 0.56)) == []
    out = nav.update(*lerp(A, B, 0.85))                  # 15 m
    assert out[0].extra_clips == ["dist_10"]


def test_skipped_thresholds_not_replayed(nav):
    out = nav.update(*lerp(A, B, 0.88))                  # jump straight to 12 m
    assert len(out) == 1 and out[0].extra_clips == ["dist_10"]
    assert nav.update(*lerp(A, B, 0.89)) == []


def test_crossing_handoff_blocks_until_confirmed(nav):
    out = nav.update(*lerp(A, B, 0.97))                  # 3 m before the crossing
    assert [o.kind for o in out] == ["crossing"]
    assert nav.waiting_crossing == 1
    assert nav.update(*lerp(B, C, 0.5)) == []            # nothing while waiting
    assert [o.prompt_id for o in nav.confirm_crossing()] == ["crossing_confirmed"]
    assert all(o.kind != "crossing" for o in nav.update(*lerp(B, C, 0.5)))


def test_arrival(nav):
    out = nav.update(*lerp(C, D, 0.95))
    assert [o.kind for o in out] == ["arrived"]
    assert not nav.active


def test_off_route_needs_several_fixes(nav):
    far = (A[0] - 0.001, A[1])  # ~100 m west
    assert nav.update(*far) == []
    assert nav.update(*far) == []
    assert [o.kind for o in nav.update(*far)] == ["reroute"]


def walk(nav, start, end, metres, step_m=1.3):
    """One fix every step_m from start towards end. Returns every prompt on the way."""
    length = haversine_m(*start, *end)
    said, gone = [], 0.0
    while gone <= metres:
        said += [o.prompt_id for o in nav.update(*lerp(start, end, gone / length))]
        gone += step_m
    return said


def test_walking_back_along_the_route_is_told_to_turn_round(nav):
    assert walk(nav, A, B, 30) == []                     # 30 m the right way
    back = walk(nav, lerp(A, B, 0.30), A, 25)            # then 25 m back down the same street
    assert back == ["turn_around"]
    assert nav.active and nav.position.off_route_m < 1   # still on the route: distance alone cannot show it
    assert walk(nav, lerp(A, B, 0.05), B, 35) == []      # turned round: no more reminders


def test_reminder_repeats_while_the_user_keeps_walking_back(nav):
    walk(nav, A, B, 70)
    assert walk(nav, lerp(A, B, 0.70), A, 50).count("turn_around") == 2


def test_gps_jitter_while_standing_or_walking_on_is_not_walking_back(nav):
    here = lerp(A, B, 0.30)
    walk(nav, A, B, 30)
    for index in range(120):                             # two minutes on the spot, 4 m of jitter each way
        shift = (4 if index % 2 else -4) / 100
        assert nav.update(*lerp(A, B, 0.30 + shift)) == [], index
    assert walk(nav, here, lerp(A, B, 0.45), 15) == []


N = (LNG0, LAT0 + 1.4 * DLAT_100M)  # 40 m north of B: where a user ends up who walks past the turn
BACK_JSON = {"status": "1", "info": "OK", "route": {"paths": [{"distance": "100", "duration": "80", "steps": [
    {"instruction": "向南步行40米左转", "distance": "40", "polyline": pl(N, B), "action": "左转",
     "assistant_action": [], "road": []},
    {"instruction": "向东步行60米到达目的地", "distance": "60", "polyline": pl(B, D), "action": [],
     "assistant_action": "到达目的地", "road": []}]}]}}
E = (LNG0 + 2 * DLNG_60M, LAT0 + DLAT_100M)  # 120 m east of B
ON_JSON = {"status": "1", "info": "OK", "route": {"paths": [{"distance": "190", "duration": "150", "steps": [
    {"instruction": "向北步行70米右转", "distance": "70", "polyline": pl(lerp(A, B, 0.3), B), "action": "右转",
     "assistant_action": [], "road": []},
    {"instruction": "向东步行120米到达目的地", "distance": "120", "polyline": pl(B, E), "action": [],
     "assistant_action": "到达目的地", "road": []}]}]}}


# The same corner as ROUTE_JSON without the crossing, which would hold the route for K1.
CORNER_JSON = {"status": "1", "info": "OK", "route": {"paths": [{"distance": "160", "duration": "128", "steps": [
    {"instruction": "向北步行100米右转", "distance": "100", "polyline": pl(A, B), "action": "右转",
     "assistant_action": [], "road": []},
    {"instruction": "向东步行60米到达目的地", "distance": "60", "polyline": pl(B, D), "action": [],
     "assistant_action": "到达目的地", "road": []}]}]}}


def test_user_who_walked_past_a_turn_is_told_to_turn_round_when_the_new_route_arrives():
    n = Navigator(NavCfg(announce_m=[50, 20, 8], arrive_m=8, offroute_m=25))
    n.start(parse_walking(CORNER_JSON), D)
    assert walk(n, lerp(A, B, 0.6), B, 40) == ["turn_right"] * 3
    said = walk(n, B, N, 40)                             # straight on past the turn
    assert said == ["nav_offroute"]
    assert n.rerouted() == []                            # nothing to say until the new route is in
    n.start(parse_walking(BACK_JSON), D)
    assert [(o.prompt_id, o.kind) for o in n.rerouted()] == [("turn_around", "prompt")]


def test_new_route_that_goes_on_ahead_gives_its_first_instruction_at_once():
    n = Navigator(NavCfg(announce_m=[50, 20, 8], arrive_m=8, offroute_m=25))
    n.start(parse_walking(ROUTE_JSON), D)
    walk(n, A, B, 30)                                    # heading north
    n.invalidate()
    n.start(parse_walking(ON_JSON), E)
    out = n.rerouted()
    assert [(o.prompt_id, o.extra_clips, o.direction) for o in out] == [("turn_right", ["dist_50"], Direction.RIGHT)]


def test_new_route_with_a_turn_close_by_leaves_it_to_the_usual_announcement():
    n = Navigator(NavCfg(announce_m=[50, 20, 8], arrive_m=8, offroute_m=25))
    n.start(parse_walking(ROUTE_JSON), D)
    n.invalidate()
    n.start(parse_walking(BACK_JSON), D)                 # 40 m to the turn, and no travel direction known
    assert n.rerouted() == []


def test_bad_accuracy_ignored(nav):
    assert nav.update(*lerp(A, B, 0.55), accuracy_m=80) == []


def test_tts_text_only_when_enabled():
    n = Navigator(NavCfg(read_route_with_tts=True))
    n.start(parse_walking(ROUTE_JSON), D)
    out = n.update(*lerp(A, B, 0.55))
    assert out[0].text == "向北步行100米右转"
