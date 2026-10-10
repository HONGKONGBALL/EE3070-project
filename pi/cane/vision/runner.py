"""Owns both cameras in one thread so stereo scans and odometry never fight
over /dev/video*.

- While walking: left camera frames (~10 Hz) -> HeadingTracker.
- When the app requests a scan (user stationary): one stereo pair -> ScanResult.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


class VisionRunner:
    def __init__(self, stereo, heading=None, odom_hz: float = 10.0) -> None:
        self.stereo = stereo      # vision.stereo.StereoDepth (or any DepthSource with .left)
        self.heading = heading    # vision.odometry.HeadingTracker or None
        self.odom_period = 1.0 / odom_hz
        self.walking = False
        self._scan_req: tuple[float, Callable] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def request_scan(self, pitch_deg: float, callback: Callable) -> None:
        with self._lock:
            self._scan_req = (pitch_deg, callback)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="vision").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        from .stereo import scan

        while not self._stop.is_set():
            with self._lock:
                req, self._scan_req = self._scan_req, None
            try:
                if req is not None:
                    req[1](scan(self.stereo, req[0]))
                elif self.walking and self.heading is not None:
                    ok, frame = self.stereo.left.read()
                    if ok:
                        import cv2
                        self.heading.process(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            except Exception as e:  # unplugged camera etc.; safety loop must keep running
                log.warning("vision error: %s", e)
                time.sleep(1.0)
            time.sleep(self.odom_period)
