"""Distance alert delivery with the real arbiter and a deterministic audio sink."""

import pytest

from cane.arbiter import OutputArbiter
from cane.model import OutputRequest, Priority
from cane.vision.distance import DistanceObservation
from cane.vision.v2_policy import V2Policy


ROI = [[0., 0.], [1., 0.], [1., 1.], [0., 1.]]


def test_near_alert_retries_when_ground_audio_outlasts_its_queue_ttl(audio, clock):
    # Given a critical ground sentence already playing when distance enters near.
    policy = V2Policy("run", ROI, {})
    arbiter = OutputArbiter(
        audio, clock=clock, max_pending=2,
        valid_request=lambda request: policy.request_valid(request, clock()),
        record_event=lambda event: policy.delivery(event.event_id, "expired", event.t)
        if event.action == "expired" else None,
    )
    arbiter.submit(OutputRequest("stairs_ahead", Priority.CRITICAL))
    policy.step(DistanceObservation("1", "pico", .8, True, clock()), clock())
    clock.advance(.1)
    first = policy.step(DistanceObservation("2", "pico", .8, True, clock()), clock())[0]
    arbiter.submit(OutputRequest(
        first["phrase_id"], Priority[first["priority"]], created=first["created_at"],
        ttl_s=first["ttl_ms"] / 1000, event_id=first["event_id"],
    ))
    # When the queued alert expires and a new, still-near sample arrives later.
    clock.advance(2.)
    policy.step(DistanceObservation("3", "pico", .8, True, clock()), clock())
    audio.finish()
    arbiter.tick()
    clock.advance(1.)
    retry = policy.step(DistanceObservation("4", "pico", .8, True, clock()), clock())

    # Then the same physical near episode gets a fresh request that can play.
    assert any(event.action == "expired" for event in arbiter.history)
    assert len(retry) == 1
    assert retry[0]["event_id"] != first["event_id"]
    arbiter.submit(OutputRequest(
        retry[0]["phrase_id"], Priority[retry[0]["priority"]],
        created=retry[0]["created_at"], event_id=retry[0]["event_id"],
    ))
    assert audio.played == [["stairs_ahead"], ["obstacle_ahead"]]


@pytest.mark.parametrize("action", ["expired", "invalid", "rejected", "replaced", "failed"])
def test_near_alert_retries_failed_attempt_only_from_a_new_sample(action):
    # Given a near alert that never reached playback.
    policy = V2Policy("run", ROI, {})
    policy.step(DistanceObservation("1", "pico", .8, True, 10.), 10.)
    first = policy.step(DistanceObservation("2", "pico", .8, True, 10.1), 10.1)[0]

    # When delivery fails, neither the old identity nor the retry delay may be bypassed.
    policy.delivery(first["event_id"], action, 10.2)
    assert policy.step(DistanceObservation("3", "pico", .8, True, 10.3), 10.3) == []
    assert policy.step(DistanceObservation("2", "pico", .8, True, 11.1), 11.1) == []
    retry = policy.step(DistanceObservation("4", "pico", .8, True, 11.2), 11.2)

    # Then fresh evidence produces one distinct retry, without replaying the old request.
    assert len(retry) == 1
    assert retry[0]["event_id"] != first["event_id"]
    assert not policy.request_valid(OutputRequest("obstacle_ahead", Priority.SAFETY,
                                                event_id=first["event_id"]), 11.2)
    assert policy.step(DistanceObservation("5", "pico", .8, True, 11.3), 11.3) == []


@pytest.mark.parametrize("action", ["started", "completed", "disabled"])
def test_near_alert_stays_handled_after_delivery(action):
    # Given a newly created near alert.
    policy = V2Policy("run", ROI, {})
    policy.step(DistanceObservation("1", "pico", .8, True, 10.), 10.)
    first = policy.step(DistanceObservation("2", "pico", .8, True, 10.1), 10.1)[0]

    # When playback starts, completes, or is explicitly disabled, then continued near is silent.
    policy.delivery(first["event_id"], action, 10.2)
    policy.delivery(first["event_id"], "queued", 10.3)
    assert policy.step(DistanceObservation("3", "pico", .8, True, 20.), 20.) == []


def test_near_alert_retries_when_started_playback_is_interrupted():
    # Given a near sentence that starts but is cut off before completion.
    policy = V2Policy("run", ROI, {})
    policy.step(DistanceObservation("1", "pico", .8, True, 10.), 10.)
    first = policy.step(DistanceObservation("2", "pico", .8, True, 10.1), 10.1)[0]
    policy.delivery(first["event_id"], "started", 10.2)

    # When it is interrupted, then newer still-near evidence permits a new attempt.
    policy.delivery(first["event_id"], "interrupted", 10.3)
    retry = policy.step(DistanceObservation("3", "pico", .8, True, 11.2), 11.2)
    assert len(retry) == 1
    policy.delivery(first["event_id"], "completed", 11.3)
    policy.delivery(retry[0]["event_id"], "rejected", 11.4)
    assert policy.step(DistanceObservation("4", "pico", .8, True, 12.3), 12.3)


def test_near_alert_does_not_retry_without_fresh_near_evidence():
    # Given an undelivered alert pending a retry.
    policy = V2Policy("run", ROI, {})
    policy.step(DistanceObservation("1", "pico", .8, True, 10.), 10.)
    first = policy.step(DistanceObservation("2", "pico", .8, True, 10.1), 10.1)[0]
    policy.delivery(first["event_id"], "expired", 10.2)

    # When readings are absent/stale and then confirm far, then the alert is not replayed.
    assert policy.step(None, 11.1) == []
    assert policy.step(DistanceObservation("3", "pico", .8, True, 10.3), 11.2) == []
    assert policy.step(DistanceObservation("4", "pico", 1.5, True, 11.3), 11.3) == []
    assert policy.step(DistanceObservation("5", "pico", 1.5, True, 11.4), 11.4) == []
    assert policy.step(DistanceObservation("6", "pico", 1.5, True, 20.), 20.) == []


def test_ground_delivery_still_reaches_the_ground_episode():
    # Given a confirmed ground event through the shared policy.
    policy = V2Policy("run", ROI, {})
    for frame_id, now in ((1, 10.), (2, 10.5)):
        policy.observe_ground("road", {
            "frame_id": frame_id, "captured_at": now,
            "detections": [{"label": "pothole", "confidence": .9,
                            "bbox_normalized": [.3, .5, .7, .9]}],
        }, now)
    event = policy.step(None, 10.5)[0]

    # When the shared delivery callback reports playback, then fresh ground stays handled.
    policy.delivery(event["event_id"], "started", 10.6)
    policy.observe_ground("road", {
        "frame_id": 3, "captured_at": 11.,
        "detections": [{"label": "pothole", "confidence": .9,
                        "bbox_normalized": [.3, .5, .7, .9]}],
    }, 11.)
    assert policy.step(None, 11.) == []
