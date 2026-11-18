"""HTTP contracts for queued controls and same-origin protection."""

from collections.abc import Mapping

import pytest
from cane.web.app import create_app
from dashboard_fakes import QueueController
from fastapi.testclient import TestClient


def dashboard_client(ctrl: QueueController) -> TestClient:
    return TestClient(create_app(ctrl, ["127.0.0.1/32"], None), client=("127.0.0.1", 5000))


@pytest.mark.parametrize("action", ["confirm", "cancel_destination", "pause", "resume", "help", "repeat", "language", "light", "test_mode", "scan", "stop_navigation", "voice_input"])
def test_control_is_queued_when_action_is_allowed(action: str) -> None:
    # Given a local diagnostics controller.
    ctrl = QueueController()
    client = dashboard_client(ctrl)
    # When a whitelisted action arrives without browser headers (CLI).
    response = client.post("/api/control", json={"action": action})
    # Then the controller receives only that action.
    assert response.status_code == 200
    assert response.json() == {"queued": True, "action": action}
    assert ctrl.controls == [action]


@pytest.mark.parametrize("action", ["clear_safety", "veto", "gpio", "", "confirm "])
def test_control_is_rejected_when_action_is_not_allowed(action: str) -> None:
    # Given a local diagnostics controller.
    ctrl = QueueController()
    # When an arbitrary command is posted.
    response = dashboard_client(ctrl).post("/api/control", json={"action": action})
    # Then the queue stays untouched.
    assert response.status_code == 422
    assert ctrl.controls == []


@pytest.mark.parametrize("path,payload", [("/api/control", {"action": "pause"}), ("/api/voice/text", {"text": "去城大"}), ("/api/sim/scenario", {"name": "clear"})])
def test_mutation_is_rejected_when_origin_is_cross_site(path: str, payload: Mapping[str, str]) -> None:
    # Given a browser visiting an unrelated origin.
    ctrl = QueueController()
    # When it attempts a local control request.
    response = dashboard_client(ctrl).post(path, json=dict(payload), headers={"Origin": "https://untrusted.example"})
    # Then no request reaches the controller.
    assert response.status_code == 403
    assert ctrl.controls == ctrl.scenarios == ctrl.texts == []


@pytest.mark.parametrize("headers", [{"Origin": "null"}, {"Origin": "http://testserver:81"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}])
def test_control_is_rejected_when_browser_origin_is_not_same(headers: Mapping[str, str]) -> None:
    # Given a local diagnostics controller.
    ctrl = QueueController()
    # When browser metadata identifies another origin.
    response = dashboard_client(ctrl).post("/api/control", json={"action": "pause"}, headers=dict(headers))
    # Then the request is blocked.
    assert response.status_code == 403
    assert ctrl.controls == []


def test_control_is_queued_when_browser_origin_matches() -> None:
    # Given a page served by the diagnostics origin.
    ctrl = QueueController()
    # When it sends a same-origin command.
    response = dashboard_client(ctrl).post("/api/control", json={"action": "pause"}, headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    # Then the action reaches the queue.
    assert response.status_code == 200
    assert ctrl.controls == ["pause"]


def test_control_is_blocked_when_client_is_not_on_allowlist() -> None:
    # Given a remote diagnostics request.
    ctrl = QueueController()
    client = TestClient(create_app(ctrl, ["127.0.0.1/32"], None), client=("192.168.1.2", 5000))
    # When it posts an otherwise valid command.
    response = client.post("/api/control", json={"action": "help"})
    # Then the queue stays untouched.
    assert response.status_code == 403
    assert ctrl.controls == []


@pytest.mark.parametrize("name", ["clear", "obstacle", "head", "drop", "step_up", "rough", "sensor_lost", "low_power", "route", "crossing", "arrival", "slope_up", "slope_down", "slope_left", "slope_right"])
def test_sim_scenario_is_queued_when_host_is_simulated(name: str) -> None:
    # Given a simulated host.
    ctrl = QueueController()
    # When a named scenario is selected.
    response = dashboard_client(ctrl).post("/api/sim/scenario", json={"name": name})
    # Then only that named scenario is queued.
    assert response.status_code == 200
    assert ctrl.scenarios == [name]


def test_sim_scenario_is_rejected_when_host_is_real() -> None:
    # Given a real host, even while test mode is active.
    ctrl = QueueController(sim=False)
    # When a simulation is requested.
    response = dashboard_client(ctrl).post("/api/sim/scenario", json={"name": "obstacle"})
    # Then real sensor state cannot be replaced.
    assert response.status_code == 409
    assert ctrl.scenarios == []


def test_sim_scenario_is_rejected_when_name_is_unknown() -> None:
    # Given a simulated host.
    ctrl = QueueController()
    # When an unlisted scenario is requested.
    response = dashboard_client(ctrl).post("/api/sim/scenario", json={"name": "run_shell"})
    # Then no scenario is queued.
    assert response.status_code == 422
    assert ctrl.scenarios == []


def test_control_is_rejected_when_extra_input_is_supplied() -> None:
    # Given a local diagnostics controller.
    ctrl = QueueController()
    # When a valid action carries an unexpected override field.
    response = dashboard_client(ctrl).post("/api/control", json={"action": "confirm", "override": True})
    # Then boundary parsing rejects the entire payload.
    assert response.status_code == 422
    assert ctrl.controls == []
