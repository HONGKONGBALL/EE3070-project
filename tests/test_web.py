import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cane.web.app import create_app  # noqa: E402


class Ctrl:
    def __init__(self, tmp_path):
        self.fixes = []
        self.texts = []
        self.log = tmp_path / "cane-20261010.jsonl"
        self.log.write_text('{"kind":"boot"}\n')

    def status(self):
        return {"state": "READY", "test_mode": True}

    def log_files(self):
        return [self.log]

    def on_location(self, lng, lat, acc):
        self.fixes.append((lng, lat, acc))

    def on_destination_text(self, text):
        self.texts.append(text)
        return {"matched": "dest1"}


@pytest.fixture
def ctrl(tmp_path):
    return Ctrl(tmp_path)


def client(ctrl, allow=("testclient/32",), token="tok123"):
    # TestClient reports host "testclient"; use a real network list in other tests
    return TestClient(create_app(ctrl, ["127.0.0.1/32"], token))


def test_diag_blocked_from_unlisted_address(ctrl):
    c = TestClient(create_app(ctrl, ["127.0.0.1/32"], "tok"))  # client host is "testclient"
    assert c.get("/api/status").status_code == 403


def test_diag_allowed_from_listed_network(ctrl):
    app = create_app(ctrl, ["127.0.0.0/8"], "tok")
    c = TestClient(app, client=("127.0.0.1", 50000))
    r = c.get("/api/status")
    assert r.status_code == 200 and r.json()["test_mode"] is True
    assert "盲杖诊断" in c.get("/").text
    assert c.get("/api/logs").json()[0]["name"] == ctrl.log.name
    assert c.get(f"/api/logs/{ctrl.log.name}").text.startswith('{"kind"')
    assert c.get("/api/logs/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert c.post("/api/voice/text", json={"text": "城大"}).json() == {"matched": "dest1"}


def test_diag_is_read_only():
    app = create_app(None, [], None)
    routes = {(r.path, m) for r in app.routes for m in getattr(r, "methods", [])}
    posts = {p for p, m in routes if m in ("POST", "PUT", "DELETE", "PATCH")}
    assert posts == {"/api/voice/text", "/api/phone/location"}
    assert not any("safety" in p or "veto" in p or "override" in p for p, _ in routes)


def test_phone_requires_token(ctrl):
    c = TestClient(create_app(ctrl, [], "tok123"), client=("192.168.43.10", 1))
    assert c.get("/phone").status_code == 403
    assert c.get("/phone?token=tok123").status_code == 200
    bad = {"lng": 114.17, "lat": 22.33, "accuracy": 5, "token": "nope"}
    assert c.post("/api/phone/location", json=bad).status_code == 403
    assert c.post("/api/phone/location", json={**bad, "token": "tok123"}).status_code == 200
    assert ctrl.fixes == [(114.17, 22.33, 5)]
    # diagnostics still closed to the phone
    assert c.get("/api/status").status_code == 403


def test_phone_disabled_without_token(ctrl):
    c = TestClient(create_app(ctrl, [], None))
    assert c.post("/api/phone/location", json={"lng": 1, "lat": 1, "token": ""}).status_code == 403


def test_bad_coordinates_rejected(ctrl):
    c = TestClient(create_app(ctrl, [], "t"))
    assert c.post("/api/phone/location", json={"lng": 500, "lat": 1, "token": "t"}).status_code == 422
