"""Camera observations can expire without overriding the safety hold."""

from cane.arbiter import OutputArbiter
from cane.model import OutputRequest, Priority


def test_superseded_vision_request_is_removed_from_queue(audio, clock):
    # Given a vision observation queued behind a more important stop warning.
    arbiter = OutputArbiter(audio, clock=clock)
    arbiter.submit(OutputRequest("stop_now", Priority.CRITICAL))
    arbiter.submit(OutputRequest("vision_person", Priority.SAFETY))
    # When a fresh frame no longer supports that classification.
    arbiter.discard_vision_observations(set())
    # Then the stale object cannot be announced after the stop clip ends.
    assert "vision_person" not in arbiter.pending()


def test_invalidated_playing_vision_stops_but_keeps_safety_hold(audio, clock):
    # Given a playing object warning and a navigation instruction waiting.
    arbiter = OutputArbiter(audio, clock=clock)
    arbiter.submit(OutputRequest("vision_chair", Priority.SAFETY))
    arbiter.submit(OutputRequest("go_straight", Priority.NAV))
    # When the camera loses that observation.
    arbiter.discard_vision_observations(set())
    # Then its audio stops, but navigation still waits for the safety hold.
    assert audio.stopped == 1
    assert arbiter.safety_hold_active()
    assert arbiter.tick() is None
    assert ["go_straight"] not in audio.played


def test_vision_invalidation_retains_range_hazards_and_supported_labels(audio, clock):
    # Given pending warnings from two independent sensor sources.
    arbiter = OutputArbiter(audio, clock=clock)
    arbiter.submit(OutputRequest("stop_now", Priority.CRITICAL))
    arbiter.submit(OutputRequest("vision_person", Priority.SAFETY))
    arbiter.submit(OutputRequest("vision_bag", Priority.SAFETY))
    arbiter.submit(OutputRequest("obstacle_head", Priority.SAFETY))
    # When the next camera frame still supports a person but not a bag.
    arbiter.discard_vision_observations({"vision_person"})
    # Then camera expiry cannot remove a real ranging hazard.
    assert arbiter.pending() == ["vision_person", "obstacle_head"]
