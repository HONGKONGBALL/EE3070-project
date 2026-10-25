"""One V4L2 reader; consumers receive the latest frame, never a backlog."""

from __future__ import annotations

import threading
import time

import cv2


class Camera:
    def __init__(self, device: str, width: int, height: int, fps: int):
        self.device, self.width, self.height, self.fps = device, width, height, fps
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.frame = None
        self.sequence = 0
        self.captured_at = 0.0
        self.error = "Starting camera"
        self.thread = threading.Thread(target=self._run, daemon=True, name="camera")

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join(timeout=3)

    def next_frame(self, after: int):
        with self.condition:
            self.condition.wait_for(
                lambda: self.sequence > after or self.stopped.is_set(), timeout=1
            )
            if self.frame is None or self.sequence <= after:
                return None
            if time.monotonic() - self.captured_at > 1.5:
                return None
            return self.frame.copy(), self.sequence, self.captured_at

    def status(self):
        with self.condition:
            age = time.monotonic() - self.captured_at if self.captured_at else None
            return {
                "ready": not self.error and age is not None and age <= 1.5,
                "error": self.error,
                "frame_id": self.sequence,
                "frame_age_ms": round(age * 1000) if age is not None else None,
                "device": self.device,
            }

    def _run(self):
        while not self.stopped.is_set():
            cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            try:
                if not cap.isOpened():
                    raise RuntimeError("Camera could not be opened")
                # C270 supports raw YUYV at 640x480; avoids its malformed MJPEG markers.
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                cap.set(cv2.CAP_PROP_FPS, self.fps)
                # Two driver buffers keep USB capture continuous; consumers still see one latest frame.
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                while not self.stopped.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError("Camera stopped returning frames")
                    with self.condition:
                        self.frame = frame
                        self.sequence += 1
                        self.captured_at = time.monotonic()
                        self.error = ""
                        self.condition.notify_all()
            except (RuntimeError, cv2.error) as exc:
                with self.condition:
                    self.frame = None
                    self.error = str(exc)
                    self.condition.notify_all()
            finally:
                cap.release()
            self.stopped.wait(2)
