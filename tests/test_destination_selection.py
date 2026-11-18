"""A spoken place becomes a route target only after candidate selection."""

import sys
from pathlib import Path

from cane.app import CaneApp
from cane.config import Destination
from cane.web.app import create_app
from fastapi.testclient import TestClient

pytest_plugins = ("test_app",)


def test_spoken_place_lists_choices_without_staging_route(app: CaneApp) -> None:
    app.cfg.voice.candidate_selection = True
    app.cfg.nav.destinations = [
        Destination("east", "东门", ["大学入口"], 114.17, 22.33),
        Destination("west", "西门", ["大学入口"], 114.18, 22.33),
    ]

    app.on_destination_text("我 想 去 大学 入口")

    voice = app.destination_search.snapshot()
    assert voice["search_state"] == "results"
    assert [candidate["id"] for candidate in voice["candidates"]] == ["east", "west"]
    assert app.pending_dest is None


def test_selection_rejects_stale_search_and_stages_current_candidate(app: CaneApp) -> None:
    app.cfg.voice.candidate_selection = True
    app.on_destination_text("城大")
    old_search = app.destination_search.snapshot()["search_id"]
    app.on_destination_text("城大")
    new_search = app.destination_search.snapshot()["search_id"]

    assert not app.select_destination(old_search, "dest1")["accepted"]
    assert app.select_destination(new_search, "dest1")["accepted"]

    assert app.pending_dest is not None and app.pending_dest.id == "dest1"
    assert not app.navigator.active


def test_dashboard_selects_current_place_through_control_boundary(app: CaneApp) -> None:
    app.cfg.voice.candidate_selection = True
    web = create_app(app, ["127.0.0.1/32"], None)
    with TestClient(web, client=("127.0.0.1", 50000)) as client:
        search = client.post("/api/voice/text", json={"text": "带我去城大"})
        assert search.json()["accepted"]
        voice = client.get("/api/status").json()["voice"]
        choice = {"search_id": voice["search_id"], "candidate_id": voice["candidates"][0]["id"]}

        forbidden = client.post("/api/voice/select", json=choice, headers={"Origin": "https://elsewhere.invalid"})
        assert forbidden.status_code == 403
        assert app.pending_dest is None
        response = client.post("/api/voice/select", json=choice)

        assert response.json() == {"accepted": True, "name": "香港城市大学"}
        assert client.get("/api/status").json()["voice"]["search_state"] == "selected"
        assert app.pending_dest is not None
        assert not app.navigator.active


def test_external_place_worker_returns_only_selected_coordinates(app: CaneApp, tmp_path: Path) -> None:
    worker = tmp_path / "places_fixture.py"
    worker.write_text(
        'import json, sys\n'
        'request = json.load(sys.stdin)\n'
        'place = {"id": "poi1", "name": "大学南门", "address": "达之路"}\n'
        'if request == {"query": "大学", "city": "香港"}:\n'
        '    print(json.dumps({"status": "ok", "candidates": [place]}))\n'
        'elif request == {"id": "poi1"}:\n'
        '    place.update(lng=114.17, lat=22.33, coordinate_frame="GCJ02")\n'
        '    print(json.dumps({"status": "ok", "place": place}))\n'
        'else:\n'
        '    raise SystemExit(2)\n',
        encoding="utf-8",
    )
    app.cfg.voice.candidate_selection = True
    app.cfg.voice.places_python = sys.executable
    app.cfg.voice.places_script = str(worker)

    app.on_destination_text("我想去大学")
    app.step()
    visible = app.destination_search.snapshot()
    assert visible["candidates"] == [{"id": "poi1", "name": "大学南门", "address": "达之路"}]
    assert app.pending_dest is None
    assert app.select_destination(visible["search_id"], "poi1")["accepted"]
    assert not app.enqueue_control("confirm")["accepted"]
    app.step()

    assert app.pending_dest is not None
    assert app.pending_dest.coordinate_frame == "GCJ02"
    assert app.pending_dest.name == "大学南门"
    assert not app.navigator.active


def test_queued_confirmation_cannot_apply_to_a_later_selection(app: CaneApp) -> None:
    app.cfg.voice.candidate_selection = True
    app.on_destination_text("城大")
    first = app.destination_search.snapshot()["search_id"]
    assert app.select_destination(first, "dest1")["accepted"]
    assert app.enqueue_control("confirm")["accepted"]

    app.on_destination_text("九龙塘")
    second = app.destination_search.snapshot()["search_id"]
    assert app.select_destination(second, "dest2")["accepted"]
    app.step()

    assert app.pending_dest is not None and app.pending_dest.id == "dest2"
    assert not app.navigator.active


def test_mac_candidate_keeps_coordinates_private_until_selection(app: CaneApp, tmp_path: Path) -> None:
    worker = tmp_path / "apple_places_fixture.py"
    worker.write_text(
        'import json, sys\n'
        'request = json.load(sys.stdin)\n'
        'assert request == {"query": "24/7 Fitness", "city": "香港"}\n'
        'print(json.dumps({"status": "ok", "candidates": [{'
        '"id": "club", "name": "24/7 FITNESS", "address": "达之路", '
        '"lng": 114.17, "lat": 22.33, "coordinate_frame": "WGS84"}]}))\n',
        encoding="utf-8",
    )
    app.destination_only = True
    app.cfg.voice.candidate_selection = True
    app.cfg.voice.places_python = sys.executable
    app.cfg.voice.places_script = str(worker)

    app.on_destination_text("附近最近的 247 健身房")
    app.step()
    voice = app.destination_search.snapshot()
    assert voice["candidates"] == [{"id": "club", "name": "24/7 FITNESS", "address": "达之路"}]
    assert app.pending_dest is None

    assert app.select_destination(voice["search_id"], "club")["accepted"]
    assert app.pending_dest is not None
    assert app.pending_dest.coordinate_frame == "WGS84"
    assert app.pending_dest.address == "达之路"


def test_apple_candidates_rank_with_phone_fix_before_limiting_result_count(
    app: CaneApp, tmp_path: Path,
) -> None:
    worker = tmp_path / "nearby_places_fixture.py"
    worker.write_text(
        'import json, sys\n'
        'request = json.load(sys.stdin)\n'
        'assert request["query"] == "24/7 Fitness" and request["city"] is None\n'
        'assert "lng" not in request and "lat" not in request\n'
        'assert request.get("region") == ({"lng": 114.2, "lat": 22.3} if request.get("nearby") else None)\n'
        'candidates = [{"id": str(i), "name": f"Club {i}", "address": "香港", '
        '"lng": 114.17 + i / 100, "lat": 22.33, "coordinate_frame": "WGS84"} '
        'for i in reversed(range(8))]\n'
        'available = candidates if request.get("nearby") else candidates[:5]\n'
        'print(json.dumps({"status": "ok", "candidates": available}))\n',
        encoding="utf-8",
    )
    app.destination_only = True
    app.cfg.voice.candidate_selection = True
    app.cfg.voice.places_source = "apple"
    app.cfg.voice.places_python = sys.executable
    app.cfg.voice.places_script = str(worker)
    app.on_destination_text("247 健身房")
    app.step()
    assert app.status()["voice"]["candidates"][0]["id"] == "7"

    web = create_app(app, ["127.0.0.1/32"], "phone-fixture")
    with TestClient(web) as phone:
        received = phone.post("/api/phone/location", json={
            "token": "phone-fixture", "lng": 114.17, "lat": 22.33, "accuracy": 12.0,
        })
        assert received.json() == {"ok": True}
    app.step()

    voice = app.status()["voice"]
    assert voice["distance_ordered"] and voice["phone_location_ready"]
    assert [choice["id"] for choice in voice["candidates"]] == ["0", "1", "2", "3", "4"]
    assert voice["candidates"][0]["distance_m"] == 0
    assert app.status()["nav"]["location"] is None


def test_origin_selection_reruns_saved_search_and_can_return_to_phone(app: CaneApp, tmp_path: Path) -> None:
    worker = tmp_path / "origin_places_fixture.py"
    worker.write_text(
        'import json, sys\n'
        'request = json.load(sys.stdin)\n'
        'assert request["city"] is None\n'
        'point = {"id": "place", "name": request["query"], "address": "深圳",'
        ' "lng": 113.95, "lat": 22.52, "coordinate_frame": "WGS84"}\n'
        'print(json.dumps({"status": "ok", "candidates": [point]}))\n',
        encoding="utf-8",
    )
    app.destination_only = True
    app.cfg.voice.candidate_selection = True
    app.cfg.voice.places_source = "apple"
    app.cfg.voice.places_python = sys.executable
    app.cfg.voice.places_script = str(worker)
    app.on_location(114.17, 22.33, 12.0)
    app.step()
    app.on_destination_text("247 健身房")
    app.step()
    initial = app.destination_search.id

    app.on_destination_text("把定位改到深圳湾口岸")
    app.step()
    origin = app.status()["voice"]["origin_search"]
    assert origin["candidates"][0]["name"] == "深圳湾口岸"
    assert app.select_origin(origin["search_id"], "place")["accepted"]
    app.step()

    assert app.destination_search.id != initial
    assert app.destination_search.query == "24/7 Fitness"
    assert app.status()["voice"]["search_origin"] == {"kind": "manual", "name": "深圳湾口岸"}
    assert app.use_phone_origin()["accepted"]
    app.step()
    assert app.status()["voice"]["search_origin"] == {"kind": "iphone"}


def test_nearby_request_without_fix_asks_for_origin(app: CaneApp) -> None:
    app.destination_only = True
    app.cfg.voice.candidate_selection = True
    app.cfg.voice.places_source = "apple"
    app.cfg.voice.places_script = "/fixture/no-search-until-origin"

    result = app.on_destination_text("附近最近的 247 健身房")

    assert result["search_state"] == "waiting_origin"
    assert app.destination_search.query == "24/7 Fitness"
    assert app.destination_search.choices == ()
