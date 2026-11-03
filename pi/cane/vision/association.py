"""Short-lived image tracks and conservative, explicitly calibrated association."""

from dataclasses import dataclass
import cv2
import numpy as np

OBJECT_LABELS = {"person", "chair", "bicycle", "backpack", "handbag", "suitcase"}


def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0, x2 - x1) * max(0, y2 - y1)
    return overlap / max(1e-9, (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - overlap)


def in_region(box, polygon):
    x1, y1, x2, y2 = box
    return cv2.pointPolygonTest(np.asarray(polygon, np.float32), ((x1+x2)/2, y2), False) >= 0


def ground_overlaps_region(box,polygon):
    """Flat hazards can intrude into the path even when their bottom-center is outside it."""
    x1,y1,x2,y2=box
    footprint=np.asarray([[x1,y1],[x2,y1],[x2,y2],[x1,y2]],np.float32)
    overlap,_=cv2.intersectConvexConvex(footprint,np.asarray(polygon,np.float32))
    return overlap>=.1*max(1e-9,(x2-x1)*(y2-y1))


@dataclass
class Track:
    track_id: str
    label: str
    bbox: list
    confidence: float
    first_seen: float
    last_seen: float
    captured_at: float
    frame_id: int
    hits: int = 1
    present: bool = True
    announced: bool = False
    raw_label: str = ""
    evidence: dict | None = None
    first_captured_at: float | None = None

    def __post_init__(self):
        if self.first_captured_at is None:self.first_captured_at=self.captured_at


class TrackBank:
    def __init__(self, prefix, window=1.0, retain=2.0):
        self.prefix, self.window, self.retain = prefix, window, retain
        self.tracks = []
        self.sequence = 0
        self.last_frame = None
        self.model_id = prefix
        self.model_revision = None

    def update(self, detections, frame_id, captured, now):
        if frame_id == self.last_frame:
            return self.tracks
        self.last_frame = frame_id
        self.tracks = [t for t in self.tracks if now-t.last_seen <= self.retain]
        previous = {t.track_id: t.present for t in self.tracks}
        for t in self.tracks:
            t.present = False
        for detection in detections:
            matches = [t for t in self.tracks if not t.present and t.label == detection["label"]
                       and iou(t.bbox, detection["bbox_normalized"]) >= 0.25]
            if matches:
                t = max(matches, key=lambda t: iou(t.bbox, detection["bbox_normalized"]))
                t.hits = t.hits + 1 if previous.get(t.track_id) and now-t.last_seen <= self.window else 1
                t.bbox, t.confidence = detection["bbox_normalized"], detection["confidence"]
                t.raw_label = detection.get("raw_label",detection["label"])
                t.evidence = detection.get("evidence")
                t.last_seen, t.captured_at, t.frame_id, t.present = now, captured, frame_id, True
            else:
                self.sequence += 1
                self.tracks.append(Track(f"{self.prefix}-{self.sequence}", detection["label"],
                                         detection["bbox_normalized"], detection["confidence"],
                                         now, now, captured, frame_id,
                                         raw_label=detection.get("raw_label",detection["label"]),
                                         evidence=detection.get("evidence")))
        return self.tracks

    def clear(self):
        self.tracks.clear()


def associate(sample, tracks, calibration, now):
    if sample is None or not sample.fresh(now) or sample.direction != "front":
        return None
    if not calibration.get("verified") or sample.calibration_id != calibration.get("id"):
        return None
    if (calibration.get("source") != sample.source or
            not calibration.get("beam_roi")):
        return None
    candidates = []
    for t in tracks:
        if (t.present and t.hits >= 3 and now-t.captured_at <= 0.5
                and abs(sample.received_monotonic-t.captured_at) <= 0.25):
            x1, y1, x2, y2 = t.bbox
            # A measured sensor beam region must overlap the object; not "largest box wins".
            beam = np.asarray(calibration["beam_roi"], np.float32)
            box = np.asarray([[x1,y1],[x2,y1],[x2,y2],[x1,y2]], np.float32)
            overlap, _ = cv2.intersectConvexConvex(beam, box)
            if overlap > 0:
                candidates.append(t)
    return candidates[0] if len(candidates) == 1 else None
