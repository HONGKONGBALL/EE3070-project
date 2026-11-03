"""Ground events independently; object speech only for fresh proximity input."""

import threading
from typing import Final, Literal, TypeAlias, assert_never

from .association import OBJECT_LABELS, TrackBank, associate, in_region, ground_overlaps_region
from .distance import ProximityGate
from .episodes import HazardEpisodes

# Export known labels for existing diagnostic consumers; spoken text comes from episode configuration.
from .episodes import PHRASES

# Each hazard has one prediction source. Context labels are not hazard decisions.
GROUND_TASK_LABELS = {"road":{"damaged_pavement"}, "pothole":{"pothole"},
                      "puddle":{"possible_puddle"}, "semantic":{"stairs"}, "stairs":{"downstairs"}}
NEAR_RETRY_S: Final = 0.8
DeliveryAction: TypeAlias = Literal[
    "queued", "started", "completed", "disabled", "expired", "invalid",
    "rejected", "replaced", "interrupted", "failed",
]


def task_hazards(name, result):
    if result is None:
        return None
    return {**result, "detections":[d for d in result["detections"] if d["label"] in GROUND_TASK_LABELS[name]]}


def support_potholes(result, semantic, context_valid, indoor_floor):
    """Strong detector evidence may survive uncertain outdoor semantics, not a known floor."""
    if result is None:
        return result
    if indoor_floor:
        return None
    if context_valid:
        return result
    if (semantic is None or
            abs(result["captured_at"]-semantic["captured_at"]) > .75):
        return None
    return {**result,"detections":[d for d in result["detections"] if d["confidence"] >= .7]}


class V2Policy:
    def __init__(self, run_id, roi, calibration, enter=1.0, leave=1.2, ground_roi=None, warning=None, record=None):
        self.run_id, self.roi, self.calibration = run_id, roi, calibration
        self.ground_roi = ground_roi if ground_roi is not None else roi
        self.objects = TrackBank("object")
        self.episodes = HazardEpisodes(run_id, warning, record)
        self.gate = ProximityGate(enter, leave)
        self.sample = None
        self.last_emit = {}
        self.live_events = {}
        self.near_announced = 0
        self.near_completed = 0
        self.near_episode = 0
        self.near_attempt = 0
        self.near_event_id = ""
        self.near_attempt_sample_id: str | None = None
        self.near_attempt_received_at = -100.0
        self.near_retry_at = -100.0
        self._delivery_lock = threading.RLock()

    def observe_objects(self, result, now):
        if result is None or now-result["captured_at"] > 0.5:
            self.objects.clear()
            return
        candidates = [d for d in result["detections"] if d["label"] in OBJECT_LABELS
                      and in_region(d["bbox_normalized"], self.roi)]
        self.objects.model_id = result.get("model_id","objects")
        self.objects.update(candidates, result["frame_id"], result["captured_at"], now)

    def observe_ground(self, name, result, now, *, negative_reliable=True):
        if result is not None:
            result = {**result, "detections": [d for d in result["detections"]
                      if d["label"] in PHRASES and ground_overlaps_region(d["bbox_normalized"], self.ground_roi)]}
        self.episodes.observe(name, result, now, negative_reliable=negative_reliable)

    def step(self, sample, now):
        with self._delivery_lock:
            self.sample = sample
            self.gate.update(sample, now)
            if self.near_episode != self.gate.episode:
                self.near_episode = self.gate.episode
                self.near_attempt = 0
                self.near_event_id = ""
                self.near_attempt_sample_id = None
                self.near_attempt_received_at = -100.0
                self.near_retry_at = -100.0
            events = self.episodes.step(now)
            for event in events:
                self.live_events[event["event_id"]] = ("ground", event["event_id"])
            match=associate(sample,self.objects.tracks,self.calibration,now) if sample and sample.fresh(now) and self.gate.near else None
            # With a verified projection only, give an in-flight visual result one short render interval.
            # Expired/missing/ambiguous evidence still becomes generic; synchronization bounds never widen.
            awaiting_visual=bool(self.calibration.get('verified') and self.calibration.get('beam_roi')
                                 and sample and sample.direction=='front' and self.calibration.get('source')==sample.source
                                 and self.calibration.get('id')==sample.calibration_id
                                 and match is None and now-self.gate.entered_at<.1)
            if (self.gate.near and self.sample and self.sample.fresh(now) and not awaiting_visual
                    and self.gate.episode != self.near_announced and not self.near_event_id
                    and now >= self.near_retry_at
                    and sample.sample_id != self.near_attempt_sample_id
                    and sample.received_monotonic > self.near_attempt_received_at
                    and (not self.near_attempt or sample.distance_m <= self.gate.enter)
                    and (self.near_attempt > 0 or now-self.last_emit.get("near", -100) >= 8)):
                label = match.label if match else None
                phrase = ("backpack_ahead" if label in {"handbag","suitcase","backpack"}
                          else label+"_ahead" if label else "obstacle_ahead")
                text = ("Bag ahead." if phrase == "backpack_ahead" else
                        label.capitalize()+" ahead." if label else "Obstacle ahead.")
                suffix = f"near:{self.gate.episode}"
                if self.near_attempt:
                    suffix += f":attempt-{self.near_attempt+1}"
                event = self._event(suffix, "OBJECT_NEAR", phrase, text, "SAFETY", now)
                event.update(object_class=label, source="vision+"+sample.source+"_distance",
                             distance_m=sample.distance_m, distance_sample_id=sample.sample_id,
                             association=("matched_simulation" if sample.source == "mock" else "matched_calibration") if match else "unmatched",
                             calibration_id=sample.calibration_id if match else None,
                             track_id=match.track_id if match else None, frame_id=match.frame_id if match else None)
                event.update(distance_received_at=sample.received_monotonic,
                             decision_trigger_received_at=self.gate.entered_sample_received_at,
                             distance_decision_latency_ms=round((now-self.gate.entered_sample_received_at)*1000,1),confirmed_at=now,
                             first_visible_captured_at=match.first_captured_at if match else None)
                event.update(source_model=self.objects.model_id if match else None,
                             raw_label=match.raw_label if match else None)
                self.live_events[event["event_id"]] = ("object", self.gate.episode, match.track_id if match else None)
                events.append(event)
                if not self.near_attempt:
                    self.last_emit["near"] = now
                self.near_attempt += 1
                self.near_event_id = event["event_id"]
                self.near_attempt_sample_id = sample.sample_id
                self.near_attempt_received_at = sample.received_monotonic
            # Only a bounded number of currently relevant events are needed for queued validation.
            self.live_events = {key: value for key,value in self.live_events.items() if self._valid(value, now)}
            return events

    def _event(self, suffix, kind, phrase, text, priority, now):
        return {"schema_version":"2.0", "event_id":f"{self.run_id}:{suffix}", "kind":kind,
                "phrase_id":phrase, "text":text, "priority":priority, "created_at":now,"ttl_ms":1500}

    def _valid(self, value, now):
        if value[0] == "ground":
            return self.episodes.valid(value[1], now)
        if not (self.sample and self.sample.fresh(now) and self.gate.near and value[1] == self.gate.episode):
            return False
        if value[2] is None:
            return True
        match = associate(self.sample, self.objects.tracks, self.calibration, now)
        return match is not None and match.track_id == value[2]

    def request_valid(self, request, now):
        with self._delivery_lock:
            return not request.event_id or (request.event_id in self.live_events and self._valid(self.live_events[request.event_id], now))

    def delivery(self, event_id: str, action: DeliveryAction, now: float) -> None:
        """Consume delivered near alerts; failed attempts need a newer distance sample."""
        with self._delivery_lock:
            if not event_id or event_id != self.near_event_id:
                self.episodes.delivery(event_id, action, now)
                return
            match action:
                case "started":
                    self.near_announced = self.near_episode
                case "completed" | "disabled":
                    self.near_announced = self.near_completed = self.near_episode
                case "expired" | "invalid" | "rejected" | "replaced" | "interrupted" | "failed":
                    self.live_events.pop(event_id, None)
                    self.near_event_id = ""
                    self.near_announced = self.near_completed
                    self.near_retry_at = now + NEAR_RETRY_S
                case "queued":
                    pass
                case unreachable:
                    assert_never(unreachable)

    def clear_visual(self, now=None):
        import time
        self.objects.clear()
        self.episodes.suspend(time.monotonic() if now is None else now)
