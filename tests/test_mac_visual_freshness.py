"""Vision observations retain capture time across the controller queue."""

from cane.app import CaneApp
from cane.config import load_config
from cane.mac.vision import MacVisionFeed
from cane.pico_link import SimPicoLink


def make_feed(tmp_path, audio, clock):
    cfg = load_config(env={})
    cfg.general.log_dir = str(tmp_path)
    app = CaneApp(cfg, sim=True, audio=audio, pico=SimPicoLink(), clock=clock)
    return MacVisionFeed(app)


def test_camera_event_expires_before_controller_delivers_it(tmp_path, audio, clock):
    feed = make_feed(tmp_path, audio, clock)
    feed.queue_observation({"vision_person"}, "vision_person", clock(), 1)
    clock.advance(2)
    feed.app._jobs.get_nowait()()
    assert ["vision_person"] not in audio.played


def test_camera_failure_revokes_previously_queued_object(tmp_path, audio, clock):
    feed = make_feed(tmp_path, audio, clock)
    feed.queue_observation({"vision_person"}, "vision_person", clock(), 1)
    feed._failed("camera disconnected")
    while not feed.app._jobs.empty():
        feed.app._jobs.get_nowait()()
    assert ["vision_person"] not in audio.played


def test_reduced_supply_does_not_publish_visual_evidence(tmp_path, audio, clock):
    feed = make_feed(tmp_path, audio, clock)
    feed.app.power.update(4.4)
    feed.queue_observation({"vision_person"}, "vision_person", clock(), 1)
    feed.app._jobs.get_nowait()()
    assert ["vision_person"] not in audio.played
