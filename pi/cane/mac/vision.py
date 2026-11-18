"""Optional latest-frame camera input; all spoken warnings use the main arbiter."""

import threading
import time
from pathlib import Path
from typing import Final, Literal, TypedDict

import cv2
from pydantic import BaseModel, ConfigDict

from cane.app import CaneApp
from cane.model import OutputRequest, Priority
from cane.vision.camera import Camera
from cane.vision.detector import Detector
from cane.vision.warnings import WarningPolicy

OBJECT_PROMPTS: Final = {"person": "vision_person", "chair": "vision_chair",
                         "backpack": "vision_bag", "handbag": "vision_bag", "suitcase": "vision_bag"}


class VisualEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    object_class: Literal["person", "chair", "backpack", "handbag", "suitcase"]


class Detection(TypedDict):
    label: str
    confidence: float
    bbox: list[float]
    ahead: bool


class VisionStatus(TypedDict):
    enabled: bool
    ready: bool
    error: str
    source: str
    measurement_status: str
    frame_age_s: float | None
    detections: list[Detection]


class MacVisionFeed:
    """Own camera resources and publish fresh classification evidence only."""

    def __init__(self, app: CaneApp, index: int = 0) -> None:
        self.app = app
        self.camera = Camera(str(index), 640, 480, 15)
        self.policy = WarningPolicy([[0.25, 0.35], [0.75, 0.35], [0.95, 1], [0.05, 1]])
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._captured_at = 0.0
        self._error = "正在加载模型"
        self._detections: list[Detection] = []
        self._observation_sequence = 0
        self._thread = threading.Thread(target=self._run, daemon=True, name="mac-vision")

    def start(self) -> None:
        self.camera.start()
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self.camera.close()
        self._thread.join(timeout=3)

    def status(self) -> VisionStatus:
        with self._lock:
            age = time.monotonic() - self._captured_at if self._captured_at else None
            fresh = age is not None and age <= 1.5 and self._jpeg is not None
            return {"enabled": True, "ready": fresh and not self._error,
                    "error": self._error if fresh else self._error or "无新鲜相机结果",
                    "source": "camera", "measurement_status": "not_measured",
                    "frame_age_s": age, "detections": self._detections if fresh else []}

    def image(self) -> bytes | None:
        with self._lock:
            if time.monotonic() - self._captured_at > 1.5:
                return None
            return self._jpeg

    def _run(self) -> None:
        try:
            detector = Detector(Path("models/yolo11n"), size=416, confidence=0.45, threads=2)
        except (OSError, RuntimeError, ValueError) as error:
            self._failed(str(error))
            return
        previous = 0
        while not self._stop.is_set():
            if self.app.power.reduced:
                self._failed("供电风险：已暂停视觉推理以降低负载")
                self._stop.wait(0.25)
                continue
            sample = self.camera.next_frame(previous)
            if sample is None:
                self._failed("摄像头无新帧，检查相机权限或设备")
                continue
            frame, sequence, captured = sample
            previous = sequence
            try:
                detections: list[Detection] = detector.predict(frame)
                now = time.monotonic()
                if now - captured > 1.5:
                    self._failed("推理结果已过期")
                    continue
                height, width = frame.shape[:2]
                event = self.policy.update(detections, width, height, now)
                active = {OBJECT_PROMPTS[d["label"]] for d in detections
                          if d.get("ahead") and d["label"] in OBJECT_PROMPTS}
                prompt = OBJECT_PROMPTS[VisualEvent.model_validate(event).object_class] if event else None
                self.queue_observation(active, prompt, captured, sequence)
                for detection in detections:
                    x1, y1, x2, y2 = map(int, detection["bbox"])
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (40, 200, 80), 2)
                    cv2.putText(frame, detection["label"], (x1, max(20, y1 - 5)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (40, 200, 80), 2)
                ok, image = cv2.imencode(".jpg", frame)
                with self._lock:
                    self._jpeg = image.tobytes() if ok else None
                    self._captured_at = captured
                    self._detections, self._error = detections, ""
            except (RuntimeError, ValueError, cv2.error) as error:
                self._failed(str(error))
            self._stop.wait(0.125)

    def _failed(self, reason: str) -> None:
        with self._lock:
            changed = self._error != reason
            self._error, self._jpeg, self._detections = reason, None, []
            self._observation_sequence += 1
        self.policy.reset()
        if changed:
            self.app.logbook.write("camera_error", reason=reason)
            def invalidate() -> None:
                self.app.arbiter.discard_vision_observations(set())
                self.app.say("vision_unavailable", Priority.SYSTEM)
            self.app._jobs.put(invalidate)

    def queue_observation(self, active: set[str], prompt: str | None,
                          captured: float, sequence: int) -> None:
        """Keep capture time when crossing threads; superseded observations expire."""
        self._observation_sequence = sequence

        def deliver() -> None:
            fresh = (not self._stop.is_set() and sequence == self._observation_sequence
                     and 0 <= self.app.clock() - captured <= 1.5
                     and not self.app.power.reduced)
            self.app.arbiter.discard_vision_observations(active if fresh else set())
            if fresh and prompt is not None:
                self.app.arbiter.submit(OutputRequest(prompt, Priority.SAFETY,
                                                       created=captured, ttl_s=1.5))

        self.app._jobs.put(deliver)
