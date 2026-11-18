import json
import shutil
import ssl
import wave
from ipaddress import IPv4Address
from threading import Event
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from cane.app import CaneApp
from cane.config import load_config
from cane.model import ButtonEvent
from cane.nav.amap import AmapClient
from cane.nav.coords import wgs84_to_gcj02
from cane.voice import recorder
from cane.voice.fitness import FitnessDirectory
from cane.voice.pi_places import PiPlaceSearch
from cane.web.app import create_app
from setup_pi_phone import create_pair


def store(key, name, lng, lat, status=1):
    return {"storeId": key, "storeName": name, "longitude": lng, "latitude": lat, "status": status}


def catalogue():
    countries = [
        {"countryCode": "HK", "areaNodeList": [
            {"areaName": "香港", "stores": [store(1, "又一城", 114.174432, 22.337381)]},
        ]},
        {"countryCode": "CN", "areaNodeList": [
            {"areaName": "深圳", "stores": [
                store(2, "较远店", 114.074, 22.551),
                store(3, "最近店", 114.062, 22.548),
                store(4, "未营业店", 114.060, 22.548, status=0),
            ]},
        ]},
    ]
    return json.dumps({"code": 0, "data": {"countryNodes": countries}}).encode()


@pytest.fixture
def cane(tmp_path, clock, audio):
    cfg = load_config(env={})
    cfg.general.log_dir = str(tmp_path)
    cfg.voice.candidate_selection = True
    cfg.voice.manual_stop = True
    cfg.voice.places_source = "pi"
    places = PiPlaceSearch(None, FitnessDirectory(catalogue))
    return CaneApp(cfg, sim=True, clock=clock, audio=audio, pi_places=places, start_threads=False)


def test_phone_upload_automatically_completes_waiting_search(cane, clock):
    client = TestClient(create_app(cane, [], "test-token", origin_control=cane, paired_phone=True))
    auth = {"Authorization": "Bearer test-token"}
    assert client.get("/phone").status_code == 200
    assert client.get("/phone-assets/pi-phone.js").status_code == 200
    assert client.get("/api/status", headers=auth).status_code == 403
    assert client.get("/api/phone/voice").status_code == 403
    assert client.post("/api/phone/voice/text", json={"text": "24/7 健身房"}).status_code == 403

    result = client.post("/api/phone/voice/text", headers=auth, json={"text": "附近最近的二四七健身房"})
    assert result.json()["search_state"] == "waiting_origin"
    fix = {"lng": 114.060, "lat": 22.548, "accuracy": 8, "token": "test-token"}
    assert client.post("/api/phone/location", json=fix).json() == {"ok": True}
    cane.step()
    state = client.get("/api/phone/voice", headers=auth).json()
    voice = state["voice"]
    assert voice["phone_location_ready"]
    assert voice["distance_ordered"]
    assert [row["id"] for row in voice["candidates"]][:2] == ["fitness:3", "fitness:2"]
    assert all("lng" not in row and "lat" not in row for row in voice["candidates"])
    assert "nav" not in state
    select = {"search_id": voice["search_id"], "candidate_id": "fitness:3"}

    clock.advance(16)
    cane.step()
    assert client.post("/api/phone/voice/select", headers=auth, json=select).json()["accepted"] is False
    assert client.get("/api/phone/voice", headers=auth).json()["voice"]["search_state"] == "waiting_origin"

    client.post("/api/phone/location", json=fix)
    cane.step()
    assert client.post("/api/phone/voice/select", headers=auth, json=select).json()["accepted"] is False
    select["search_id"] = cane.status()["voice"]["search_id"]
    assert client.post("/api/phone/voice/select", headers=auth, json=select).json()["accepted"]
    assert cane.pending_dest.name == "24/7 FITNESS 最近店"


def test_k2_usb_short_toggles_recording_and_sends_recognized_query(cane, clock, monkeypatch):
    jobs = []
    monkeypatch.setattr(cane, "_spawn", lambda fn, name: jobs.append(fn))
    monkeypatch.setattr(cane, "_voice_unavailable_reason", lambda: None)

    def transcribe(generation, stop):
        assert stop is not None and stop.is_set()
        return "附近最近的二四七健身房"

    monkeypatch.setattr(cane, "_record_and_transcribe", transcribe)
    cane._on_pico(ButtonEvent(clock(), "dest", "short"))
    cane.step()
    assert cane.listening and not cane.voice_record_stop.is_set()
    cane._on_pico(ButtonEvent(clock(), "dest", "short"))
    cane.step()
    assert cane.voice_record_stop.is_set()
    assert len(jobs) == 1
    jobs.pop()()
    cane.step()
    assert cane.status()["voice"]["search_state"] == "waiting_origin"
    assert not cane.listening


def test_amap_places_use_coarse_region_and_yield_wgs84():
    origin = (114.061, 22.549)
    published = wgs84_to_gcj02(*origin)

    def fetch(url, _timeout):
        request = urlsplit(url)
        args = parse_qs(request.query)
        assert request.path == "/v3/place/around"
        assert args["keywords"] == ["健身房"]
        assert args["location"] != [f"{published[0]:.6f},{published[1]:.6f}"]
        return json.dumps({"status": "1", "pois": [
            {"id": "venue", "name": "健身房", "location": f"{published[0]},{published[1]}"},
        ]}).encode()

    result, = PiPlaceSearch(AmapClient("test", fetch=fetch)).search("健身房", origin)
    assert result.coordinate_frame == "WGS84"
    assert (result.lng, result.lat) == pytest.approx(origin, abs=1e-7)


def test_alsa_capture_closes_wave_on_stop(tmp_path, monkeypatch):
    output = tmp_path / "input.wav"
    stop = Event()

    class Capture:
        def __init__(self, command, **kwargs):
            assert command[:4] == ["arecord", "-q", "-D", "plughw:1,0"]
            assert command[-1] == str(output)

        def __enter__(self):
            stop.set()
            return self

        def __exit__(self, *args):
            pass

        def send_signal(self, signal):
            assert signal == recorder.SIGINT

        def wait(self, timeout=None):
            with wave.open(str(output), "wb") as target:
                target.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                target.writeframes(b"\x00\x01" * 500)

    monkeypatch.setattr(recorder.platform, "system", lambda: "Linux")
    monkeypatch.setattr(recorder.subprocess, "Popen", Capture)
    assert recorder.record_until_stopped(output, stop, "plughw:1,0") == output
    with wave.open(str(output), "rb") as captured:
        assert captured.getnframes() == 500


@pytest.mark.skipif(shutil.which("openssl") is None, reason="OpenSSL required")
def test_phone_pair_certificate_matches_ip(tmp_path):
    directory = tmp_path / "phone"
    address = IPv4Address("192.168.100.9")
    pair = create_pair(directory, address)
    assert create_pair(directory, address) == pair
    assert directory.stat().st_mode & 0o077 == 0
    assert not (directory / "root.key").exists()
    root = ssl.DER_cert_to_PEM_cert((directory / "root.cer").read_bytes())
    client_context = ssl.create_default_context(cadata=root)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(directory / "server.crt", directory / "server.key")
    from_server, from_client = ssl.MemoryBIO(), ssl.MemoryBIO()
    to_server, to_client = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_context.wrap_bio(from_server, to_server, server_hostname=str(address))
    server = server_context.wrap_bio(from_client, to_client, server_side=True)
    pending = {client, server}
    for _ in range(10):
        for peer in tuple(pending):
            try:
                peer.do_handshake()
            except ssl.SSLWantReadError:
                continue
            pending.remove(peer)
        from_client.write(to_server.read())
        from_server.write(to_client.read())
        if not pending:
            break
    assert not pending
