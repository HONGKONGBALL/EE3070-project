"""Small image-region policy; class reminders, not distance or route guidance."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import queue
import subprocess
import threading
import time

import cv2
import numpy as np

PHRASES = {
    "person": "Person ahead. Please be careful.",
    "chair": "Chair ahead. Please be careful.",
    "backpack": "Bag ahead. Please be careful.",
    "handbag": "Bag ahead. Please be careful.",
    "suitcase": "Bag ahead. Please be careful.",
}
PHRASE_IDS = {label: ("backpack_ahead" if label in ("handbag", "suitcase") else label + "_ahead")
              for label in PHRASES}


class WarningPolicy:
    def __init__(self, roi, cooldown=8.0):
        self.roi = np.asarray(roi, dtype=np.float32)
        self.cooldown = cooldown
        self.hits = {label: deque() for label in PHRASES}
        self.last_seen = {label: -100.0 for label in PHRASES}
        self.last_emitted = {label: -100.0 for label in PHRASES}
        self.announced = {label: False for label in PHRASES}
        self.last_event = -100.0

    def reset(self):
        for hits in self.hits.values():
            hits.clear()
        self.announced = {label: False for label in PHRASES}

    def update(self, detections, width, height, now):
        present = {}
        for d in detections:
            x1, y1, x2, y2 = d["bbox"]
            inside = cv2.pointPolygonTest(self.roi, ((x1 + x2) / (2 * width), y2 / height), False) >= 0
            d["ahead"] = (d["label"] in PHRASES and inside and
                          (x2 - x1) * (y2 - y1) / (width * height) >= 0.015)
            if d["ahead"] and d["label"] not in present:
                present[d["label"]] = d
        for label, hits in self.hits.items():
            while hits and now - hits[0] > 1.0:
                hits.popleft()
            if label in present:
                if now - self.last_seen[label] > 2:
                    hits.clear()
                    self.announced[label] = False
                hits.append(now)
                self.last_seen[label] = now
            else:
                hits.clear()
        for label, detection in present.items():
            if (len(self.hits[label]) >= 3 and not self.announced[label]
                    and now - self.last_emitted[label] >= self.cooldown
                    and now - self.last_event >= 3):
                self.last_emitted[label] = self.last_event = now
                self.announced[label] = True
                return {"kind": "OBJECT_AHEAD", "object_class": label,
                        "confidence": detection["confidence"],
                        "bbox_normalized": [round(v / (width if i % 2 == 0 else height), 4)
                                            for i, v in enumerate(detection["bbox"])],
                        "phrase_id": PHRASE_IDS[label], "text": PHRASES[label],
                        "distance_m": None, "ttl_ms": 1500}
        return None


class Audio:
    """One bounded, asynchronous playback worker. No live speech synthesis."""
    def __init__(self, directory: Path, enabled: bool, target="", record=None):
        self.directory, self.target = directory, target
        self.enabled = enabled
        self.state = "ready" if enabled else "disabled"
        self.error = ""
        self.last_played = None
        self.record = record or (lambda *_args, **_fields: None)
        self.pending = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="audio")
        self.thread.start()

    def submit(self, event):
        if self.enabled:
            try:
                self.pending.put_nowait((event, time.monotonic()))
            except queue.Full:
                pass

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=6)

    def clear_pending(self):
        try:
            self.pending.get_nowait()
        except queue.Empty:
            pass

    def _run(self):
        while not self.stopped.is_set():
            try:
                event, submitted = self.pending.get(timeout=0.2)
            except queue.Empty:
                continue
            if time.monotonic() - submitted > event.get("ttl_ms", 1500) / 1000:
                continue
            path = self.directory / (event["phrase_id"] + ".wav")
            command = ["pw-play"] + (["--target", self.target] if self.target else []) + [str(path)]
            self.state = "playing"
            try:
                started = time.monotonic()
                self.record("audio_start", phrase_id=event["phrase_id"], process_started_at=started)
                subprocess.run(command, check=True, timeout=5, capture_output=True)
                self.last_played = {"phrase_id": event["phrase_id"], "process_started_at": started,
                                    "finished_at": time.monotonic()}
                self.state, self.error = "ready", ""
                self.record("audio_done", **self.last_played)
            except (OSError, subprocess.SubprocessError) as exc:
                self.state, self.error = "error", str(exc)
                self.record("audio_error", phrase_id=event["phrase_id"], message=str(exc))
