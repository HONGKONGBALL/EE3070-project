"""Real Mac-host subprocess and loopback HTTP user workflows, no hardware."""

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def wait_status(client, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get("/api/status").json()
        if predicate(last):
            return last
        time.sleep(0.04)
    pytest.fail(f"state did not converge: {last}")


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    directory = tmp_path_factory.mktemp("mac-host")
    with (
        (directory / "host.log").open("w") as output,
        subprocess.Popen(
            [sys.executable, "-m", "cane.mac.host", "--sim", "--quiet", "--http-port", str(port)],
            cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
        ) as process,
    ):
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                deadline = time.monotonic() + 8
                while True:
                    try:
                        if client.get("/api/status").status_code == 200:
                            break
                    except httpx.TransportError:
                        if time.monotonic() >= deadline:
                            pytest.fail((directory / "host.log").read_text())
                        time.sleep(0.05)
                wait_status(client, lambda status: status["pico_ok"])
                yield client
        finally:
            process.terminate()
            process.wait(timeout=5)


def scenario(host, name):
    response = host.post("/api/sim/scenario", json={"name": name})
    assert response.status_code == 200 and response.json()["accepted"]


def control(host, action):
    response = host.post("/api/control", json={"action": action})
    assert response.status_code == 200 and response.json()["accepted"]


@pytest.mark.parametrize(("name", "hazard"), [
    ("obstacle", "stop"), ("head", "head"), ("drop", "drop"), ("step_up", "step_up")
])
def test_scenario_reaches_production_hazard_pipeline(host, name, hazard):
    # Given explicit simulation, when a synthetic sensor changes,
    # then the production assessor reports the corresponding hazard.
    scenario(host, name)
    status = wait_status(host, lambda value: hazard in value["hazards"])
    assert status["sim"] and status["host"]["mode"] == "simulation"


def test_disconnect_fault_and_reconnect_recovery(host):
    scenario(host, "sensor_lost")
    status = wait_status(host, lambda value: value["state"] == "FAULT")
    assert not status["pico_ok"] and not status["sensors"]["valid"]
    scenario(host, "clear")
    wait_status(host, lambda value: value["state"] == "READY" and value["pico_ok"])


def test_pause_cannot_resume_through_obstacle(host):
    scenario(host, "obstacle")
    wait_status(host, lambda value: "stop" in value["hazards"])
    control(host, "pause")
    wait_status(host, lambda value: value["paused"])
    control(host, "resume")
    status = wait_status(host, lambda value: value["last_control"]["action"] == "resume")
    assert status["paused"] and "hazard_present" in status["resume_blocked_reasons"]
    scenario(host, "clear")
    wait_status(host, lambda value: not value["safety_hold"] and not value["hazards"], timeout=6)
    control(host, "resume")
    wait_status(host, lambda value: not value["paused"])


def test_destination_crossing_confirmation_and_arrival(host):
    scenario(host, "route")
    wait_status(host, lambda value: value["nav"]["pending_destination"] == "演示终点")
    control(host, "confirm")
    wait_status(host, lambda value: value["nav"]["active"])
    scenario(host, "crossing")
    wait_status(host, lambda value: value["nav"]["waiting_crossing"])
    scenario(host, "arrival")
    status = wait_status(host, lambda value: value["simulation"]["last_error"] is not None)
    assert status["nav"]["waiting_crossing"]
    control(host, "confirm")
    wait_status(host, lambda value: not value["nav"]["waiting_crossing"])
    scenario(host, "arrival")
    wait_status(host, lambda value: not value["nav"]["active"])


def test_slope_uses_point_cloud_fit_and_is_marked_simulated(host):
    scenario(host, "slope_up")
    status = wait_status(host, lambda value: value["scan"] is not None)
    assert status["scan"]["uphill_deg"] == pytest.approx(8, abs=0.1)
    assert status["simulation"]["coordinate_system"] == "synthetic"


def test_supply_policy_retains_local_help_without_invented_battery_estimate(host):
    scenario(host, "low_power")
    status = wait_status(host, lambda value: value["power"]["stage"] == "critical")
    assert status["power"]["battery_percent"] is None
    control(host, "help")
    status = wait_status(host, lambda value: value["help_requested"])
    assert status["help_delivery"] == "local_only"
