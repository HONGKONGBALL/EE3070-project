import json
from pathlib import Path

import pytest

from cane.app import CaneApp
from cane.config import load_config
from cane.model import ButtonEvent, SensorFrame
from cane.nav.amap import AmapClient
from cane.pico_link import SimPicoLink
from cane.state_machine import State

from test_nav import A, B, ROUTE_JSON, lerp

GROUND = 1.482
EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "config.example.toml"


class FakeImu:
    def __init__(self):
        self.moving = False
        self.pitch_deg = None

    def window(self):
        return []


@pytest.fixture
def app(tmp_path, audio, clock, monkeypatch):
    cfg = load_config(EXAMPLE, env={})
    cfg.general.log_dir = str(tmp_path / "logs")
    cfg.audio.dir = str(tmp_path / "audio")
    cfg.nav.coord_mode = "none"
    pico = SimPicoLink()
    amap = AmapClient("k", fetch=lambda url, t: json.dumps(ROUTE_JSON).encode())
    a = CaneApp(cfg, sim=True, pico=pico, audio=audio, amap=amap, clock=clock, imu=FakeImu(),
                start_threads=False)
    a.start()
    return a


def feed(app, clock, front=None, up=None, tf=GROUND, n=1):
    for _ in range(n):
        clock.advance(0.05)
        app.pico.inbox.put(SensorFrame(clock(), front, up, tf, 900))
        app.step()


def flush(app, audio, clock, secs=3.0):
    """Let queued prompts play out."""
    t = 0.0
    while t < secs:
        audio.finish()
        t += 0.05
        feed(app, clock)  # keep the Pico alive


def test_boot_and_heartbeat(app, audio, clock):
    assert app.sm.state is State.READY
    assert audio.played[0] == ["boot_ready"]
    feed(app, clock, n=12)
    assert sum(1 for m in app.pico.sent if m == {"t": "h"}) >= 1


def test_close_obstacle_says_stop_and_vibrates(app, audio, clock):
    feed(app, clock, n=1)
    feed(app, clock, front=0.4)
    assert ["stop_now"] in audio.played
    assert {"t": "v", "p": "stop"} in app.pico.sent


def test_pico_timeout_faults_and_recovers(app, audio, clock):
    feed(app, clock, n=2)
    clock.advance(2.0)
    app.step()
    assert app.sm.state is State.FAULT
    assert ["pico_lost"] in audio.played
    feed(app, clock, n=1)
    assert app.sm.state is State.READY


def test_motion_from_imu(app, clock):
    feed(app, clock)
    app.imu.moving = True
    feed(app, clock)
    assert app.sm.state is State.WALKING
    app.imu.moving = False
    feed(app, clock)
    assert app.sm.state is State.STOPPED


def test_voice_to_navigation_to_crossing(app, audio, clock):
    feed(app, clock)
    app.on_location(*A, 5.0)
    feed(app, clock)
    r = app.on_destination_text("我要去城大")
    assert r["matched"] == "dest1"
    flush(app, audio, clock)
    assert ["dest_dest1", "dest_confirm"] in audio.played
    # OK starts navigation (the fake AMap returns the test route)
    app.on_button("ok", "short")
    feed(app, clock)
    assert app.navigator.active
    flush(app, audio, clock)
    assert ["nav_start"] in audio.played

    # stop + align, then walk towards the turn: turn is announced with distance
    app.on_button("ok", "short")
    assert app.sm.aligned
    app.on_location(*lerp(A, B, 0.55), 5.0)
    feed(app, clock)
    flush(app, audio, clock)
    assert ["turn_right", "dist_30"] in audio.played

    # crossing hand-off waits for OK
    app.on_location(*lerp(A, B, 0.97), 5.0)
    feed(app, clock)
    assert app.sm.state is State.CROSSING
    flush(app, audio, clock)
    assert ["crossing_ahead"] in audio.played
    app.on_button("ok", "short")
    feed(app, clock)
    assert app.sm.state is State.STOPPED


def test_turns_blocked_without_alignment(app, audio, clock):
    feed(app, clock)
    app.on_location(*A, 5.0)
    feed(app, clock)
    app.on_destination_text("城大")
    app.on_button("ok", "short")
    feed(app, clock)
    flush(app, audio, clock)
    app.on_location(*lerp(A, B, 0.55), 5.0)
    feed(app, clock)
    flush(app, audio, clock)
    assert ["turn_right", "dist_30"] not in audio.played
    assert ["nav_stop_align"] in audio.played


def test_location_loss_stops_navigation_but_not_safety(app, audio, clock):
    feed(app, clock)
    app.on_location(*A, 5.0)
    feed(app, clock)
    app.on_destination_text("城大")
    app.on_button("ok", "short")
    feed(app, clock)
    assert app.navigator.active
    flush(app, audio, clock, secs=20)  # 20 s without a phone fix
    assert not app.navigator.active
    assert ["net_lost_nav_off"] in audio.played
    feed(app, clock, front=0.4)
    assert audio.played[-1] == ["stop_now"]


def test_safety_interrupts_navigation_prompt(app, audio, clock):
    feed(app, clock)
    audio.finish()
    app.say("go_straight")
    assert audio.played[-1] == ["go_straight"]
    feed(app, clock, front=0.4)
    assert audio.played[-1] == ["stop_now"] and audio.stopped >= 1


def test_language_cycle(app, audio, clock):
    app.on_button("repeat", "long")
    assert app.lang == "yue"


def test_status_has_no_secrets(tmp_path, audio, clock):
    cfg = load_config(None, env={"AMAP_KEY": "TOPSECRET123", "CANE_PHONE_TOKEN": "PHONESECRET"})
    cfg.general.log_dir = str(tmp_path)
    a = CaneApp(cfg, sim=True, pico=SimPicoLink(), audio=audio, clock=clock, start_threads=False)
    a.start()
    blob = json.dumps(a.status()) + "".join(p.read_text() for p in a.log_files())
    assert "TOPSECRET123" not in blob and "PHONESECRET" not in blob
