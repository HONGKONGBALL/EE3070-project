"""Mac host behaviors, with explicit simulated evidence."""

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_simulated_obstacle_is_a_real_sensor_input() -> None:
    # Given a deterministic software-only Pico.
    from cane.mac.scenarios import ScenarioPicoLink

    link = ScenarioPicoLink()
    # When the operator selects an obstacle.
    link.select_scenario("obstacle")
    sample = link.sample(10.0)
    # Then the normal hazard pipeline receives a close obstacle.
    assert sample is not None and sample.front_m == 0.4


def test_simulated_disconnect_stops_publishing() -> None:
    from cane.mac.scenarios import ScenarioPicoLink

    link = ScenarioPicoLink()
    link.select_scenario("sensor_lost")
    assert link.sample(10.0) is None


def test_invalid_scenario_does_not_change_existing_input() -> None:
    from cane.mac.scenarios import ScenarioError, ScenarioPicoLink

    link = ScenarioPicoLink()
    with pytest.raises(ScenarioError):
        link.select_scenario("arbitrary_gpio")
    assert link.scenario == "clear"


def test_mac_audio_uses_afplay_without_alsa_options(tmp_path: Path) -> None:
    from cane.mac.audio import MacAudio

    directory = tmp_path / "cmn"
    directory.mkdir()
    (directory / "stop_now.wav").write_bytes(b"fixture")
    sink = MacAudio(tmp_path, "cmn")
    assert sink._cmds(["stop_now"], None) == [
        ["afplay", str(directory / "stop_now.wav")]
    ]


def test_missing_audio_is_reported_instead_of_silently_succeeding(tmp_path: Path) -> None:
    from cane.mac.audio import MacAudio

    sink = MacAudio(tmp_path, "cmn")
    assert sink._cmds(["stop_now"], None) == []
    assert "stop_now" in sink.error


def test_serial_selection_rejects_ambiguous_devices() -> None:
    from cane.mac.devices import DeviceSelectionError, choose_serial_port

    with pytest.raises(DeviceSelectionError):
        choose_serial_port("auto", ["/dev/cu.usbmodem1", "/dev/cu.usbmodem2"])


def test_serial_selection_never_falls_back_to_simulation() -> None:
    from cane.mac.devices import DeviceSelectionError, choose_serial_port

    with pytest.raises(DeviceSelectionError):
        choose_serial_port("auto", [])


def test_voice_serial_detection_requires_raspberry_pi_usb_id(monkeypatch) -> None:
    from cane.mac.devices import list_ports, serial_candidates

    monkeypatch.setattr(list_ports, "comports", lambda: [
        SimpleNamespace(device="/dev/cu.usbmodem1", vid=0x303A),
        SimpleNamespace(device="/dev/cu.usbserial2", vid=0x10C4),
        SimpleNamespace(device="/dev/cu.usbmodem3", vid=0x2E8A),
    ])
    assert serial_candidates(pico_only=True) == ["/dev/cu.usbmodem3"]
    assert serial_candidates() == ["/dev/cu.usbmodem3", "/dev/cu.usbserial2"]


def test_voice_bench_handoff_when_pico_plugged_after_startup(monkeypatch) -> None:
    from queue import Queue

    from cane.mac import host

    ports = []
    inbox = Queue()
    receiver = SimpleNamespace(
        port="/dev/cu.usbmodem3", inbox=inbox, connected=True, start=lambda: None,
    )
    receiver.close = lambda: setattr(receiver, "connected", False)
    monkeypatch.setattr(host, "serial_candidates", lambda *, pico_only: ports if pico_only else [])
    monkeypatch.setattr(host, "MacSerialPicoLink", lambda *args, **kwargs: receiver)
    link = host.LocalVoiceLink("auto", 115200)

    link.refresh(0)
    assert not link.connected
    ports.append(receiver.port)
    link.refresh(2)
    assert link.connected and link.inbox is inbox
    ports.clear()
    link.refresh(4)
    assert link.port is None and not receiver.connected


def test_demo_phone_refreshes_only_synthetic_location(tmp_path, audio, clock) -> None:
    from cane.app import CaneApp
    from cane.config import load_config
    from cane.mac.controller import MacController
    from cane.mac.scenarios import ScenarioPicoLink

    cfg = load_config(env={})
    cfg.general.log_dir = str(tmp_path)
    app = CaneApp(cfg, sim=True, pico=ScenarioPicoLink(), audio=audio, clock=clock)
    controller = MacController(app)
    controller._demo_location = (0.0, 0.0)
    controller.refresh_simulated_location(clock())
    app._jobs.get_nowait()()
    assert app.location == (0.0, 0.0, 2.0, clock())
