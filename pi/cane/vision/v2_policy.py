"""Ground events independently; object speech only for fresh proximity input."""

from .association import OBJECT_LABELS, TrackBank, associate, in_region, ground_overlaps_region, iou
from .distance import ProximityGate

PHRASES = {
    "pothole": ("pothole_ahead", "Possible pothole ahead. Watch your step.", "CRITICAL"),
    "damaged_pavement": ("damaged_pavement", "Damaged pavement ahead. Watch your step.", "SAFETY"),
    "possible_puddle": ("puddle_ahead", "Possible puddle ahead.", "SAFETY"),
    "stairs": ("stairs_ahead", "Possible stairs ahead.", "SAFETY"),
    "downstairs": ("downstairs_ahead", "Possible steps down ahead. Watch your step.", "CRITICAL"),
}

# Each hazard has one prediction source. Context labels are not hazard decisions.
GROUND_TASK_LABELS = {"road":{"damaged_pavement"}, "pothole":{"pothole"},
                      "puddle":{"possible_puddle"}, "semantic":{"stairs"}, "stairs":{"downstairs"}}


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
    def __init__(self, run_id, roi, calibration, enter=1.0, leave=1.2, ground_roi=None):
        self.run_id, self.roi, self.calibration = run_id, roi, calibration
        self.ground_roi = ground_roi if ground_roi is not None else roi
        self.objects = TrackBank("object")
        self.ground = {}
        self.gate = ProximityGate(enter, leave)
        self.sample = None
        self.last_emit = {}
        self.live_events = {}
        self.near_announced = 0

    def observe_objects(self, result, now):
        if result is None or now-result["captured_at"] > 0.5:
            self.objects.clear()
            return
        candidates = [d for d in result["detections"] if d["label"] in OBJECT_LABELS
                      and in_region(d["bbox_normalized"], self.roi)]
        self.objects.model_id = result.get("model_id","objects")
        self.objects.update(candidates, result["frame_id"], result["captured_at"], now)

    def observe_ground(self, name, result, now):
        bank = self.ground.setdefault(name, TrackBank(name, window=2.0))
        if result is None or now-result["captured_at"] > 1.5:
            bank.clear()
            return
        candidates = [d for d in result["detections"] if d["label"] in PHRASES
                      and ground_overlaps_region(d["bbox_normalized"], self.ground_roi)]
        bank.model_id,bank.model_revision = result.get("model_id",name),result.get("model_revision")
        bank.update(candidates, result["frame_id"], result["captured_at"], now)

    def step(self, sample, now):
        self.sample = sample
        self.gate.update(sample, now)
        events = []
        ground_tracks=[(bank,track) for bank in self.ground.values() for track in bank.tracks]
        # Specific critical warnings precede generic ones in the same decision tick.
        ground_tracks.sort(key=lambda pair:PHRASES[pair[1].label][2]!='CRITICAL')
        for bank,track in ground_tracks:
            if not (track.present and track.hits >= 2 and now-track.captured_at <= 1.5 and not track.announced):
                continue
            if track.label=='stairs' and any(
                    other.label=='downstairs' and other.present and other.hits>=2
                    and now-other.captured_at<=1.5 and iou(track.bbox,other.bbox)>=.25
                    for _,other in ground_tracks):
                track.announced=True
                continue
            phrase, text, priority = PHRASES[track.label]
            if now-self.last_emit.get(track.label, -100) < 8:
                continue
            event = self._event(f"ground:{track.track_id}", "GROUND_HAZARD", phrase, text, priority, now)
            event.update(object_class=track.label, track_id=track.track_id, frame_id=track.frame_id,
                         confidence=track.confidence, bbox_normalized=track.bbox,
                         captured_at=track.captured_at, source_model=bank.model_id,
                         first_visible_captured_at=track.first_captured_at,first_observed_at=track.first_seen,
                         confirmed_at=now,confirmation_latency_ms=round((now-track.first_captured_at)*1000,1),
                         source_revision=bank.model_revision, raw_label=track.raw_label,
                         evidence=track.evidence,
                         source="vision", distance_m=None)
            self.live_events[event["event_id"]] = ("ground", bank, track.track_id)
            events.append(event)
            track.announced = True
            self.last_emit[track.label] = now
        match=associate(sample,self.objects.tracks,self.calibration,now) if sample and sample.fresh(now) and self.gate.near else None
        # With a verified projection only, give an in-flight visual result one short render interval.
        # Expired/missing/ambiguous evidence still becomes generic; synchronization bounds never widen.
        awaiting_visual=bool(self.calibration.get('verified') and self.calibration.get('beam_roi')
                             and sample and sample.direction=='front' and self.calibration.get('source')==sample.source
                             and self.calibration.get('id')==sample.calibration_id
                             and match is None and now-self.gate.entered_at<.1)
        if (self.gate.near and self.sample and self.sample.fresh(now) and not awaiting_visual
                and self.gate.episode != self.near_announced and now-self.last_emit.get("near", -100) >= 8):
            label = match.label if match else None
            phrase = ("backpack_ahead" if label in {"handbag","suitcase","backpack"}
                      else label+"_ahead" if label else "obstacle_ahead")
            text = ("Bag ahead." if phrase == "backpack_ahead" else
                    label.capitalize()+" ahead." if label else "Obstacle ahead.")
            event = self._event(f"near:{self.gate.episode}", "OBJECT_NEAR", phrase, text, "SAFETY", now)
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
            self.last_emit["near"] = now
            self.near_announced = self.gate.episode
        # Only a bounded number of currently relevant events are needed for queued validation.
        self.live_events = {key: value for key,value in self.live_events.items() if self._valid(value, now)}
        return events

    def _event(self, suffix, kind, phrase, text, priority, now):
        return {"schema_version":"2.0", "event_id":f"{self.run_id}:{suffix}", "kind":kind,
                "phrase_id":phrase, "text":text, "priority":priority, "created_at":now,"ttl_ms":1500}

    def _valid(self, value, now):
        if value[0] == "ground":
            return any(t.track_id == value[2] and t.present and now-t.captured_at <= 1.5 for t in value[1].tracks)
        if not (self.sample and self.sample.fresh(now) and self.gate.near and value[1] == self.gate.episode):
            return False
        if value[2] is None:
            return True
        match = associate(self.sample, self.objects.tracks, self.calibration, now)
        return match is not None and match.track_id == value[2]

    def request_valid(self, request, now):
        return not request.event_id or (request.event_id in self.live_events and self._valid(self.live_events[request.event_id], now))

    def clear_visual(self):
        self.objects.clear()
        for bank in self.ground.values(): bank.clear()
