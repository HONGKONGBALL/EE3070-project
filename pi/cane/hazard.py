"""Turn raw distances from the Pico into hazards.

Sensors (all on the upper section of the cane):
- front HC-SR04, roughly horizontal
- up HC-SR04, tilted up to see head-height obstacles
- TFmini-S, by default angled down at the ground ahead ("ground" role) to see
  drop-offs, steps and range jitter. With role="front" it backs up the front sonar.

Hazards are debounced over `confirm_frames` consecutive frames.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import HazardCfg, TfminiCfg
from .model import SensorFrame
from .prompts import required_warning_distance

DEFAULT_CLIP_S = 1.0  # used until real clip durations are measured


@dataclass(frozen=True)
class Hazard:
    kind: str          # stop | front | head | drop | step_up
    prompt_id: str
    distance_m: float | None


def ground_spot_ahead_m(mount_height_m: float, tilt_down_deg: float) -> float:
    """Horizontal distance from the ranger to where its beam hits flat ground."""
    t = math.tan(math.radians(tilt_down_deg))
    return math.inf if t <= 0.05 else mount_height_m / t


def expected_ground_range(mount_height_m: float, tilt_down_deg: float) -> float:
    """Slant range from a down-tilted ranger to flat ground."""
    s = math.sin(math.radians(tilt_down_deg))
    if s <= 0.05:
        return math.inf
    return mount_height_m / s


class HazardAssessor:
    def __init__(
        self,
        hazard: HazardCfg,
        tfmini: TfminiCfg,
        clip_s: dict[str, float] | None = None,
        confirm_frames: int = 2,
    ) -> None:
        self.h = hazard
        self.tf = tfmini
        self.clip_s = clip_s or {}
        self.confirm_frames = confirm_frames
        self._streak: dict[str, int] = {}

    # ---------------------------------------------------------------- helpers
    def warn_distance(self, prompt_id: str, speed_mps: float | None = None) -> float:
        speed = self.h.walking_speed_mps if speed_mps is None else speed_mps
        return required_warning_distance(
            self.clip_s.get(prompt_id, DEFAULT_CLIP_S),
            speed,
            self.h.sensor_latency_s,
            self.h.comprehension_s,
            self.h.margin_m,
        )

    def _valid(self, d: float | None) -> float | None:
        if d is None or d <= 0.02 or d > self.h.max_valid_m:
            return None
        return d

    def ground_check(self, frame: SensorFrame, tilt_deg: float | None = None) -> str | None:
        """'drop', 'step_up' or None. tilt_deg may come from the IMU pitch."""
        if self.tf.role != "ground" or frame.tfmini_m is None:
            return None
        if frame.tfmini_strength < self.tf.min_strength:
            return None
        tilt = self.tf.tilt_down_deg if tilt_deg is None else tilt_deg
        expected = expected_ground_range(self.tf.mount_height_m, tilt)
        if frame.tfmini_m > expected + self.tf.drop_tolerance_m:
            return "drop"
        if frame.tfmini_m < expected - self.tf.rise_tolerance_m:
            return "step_up"
        return None

    # ----------------------------------------------------------------- assess
    def assess(
        self,
        frame: SensorFrame,
        moving: bool = True,
        speed_mps: float | None = None,
        tilt_deg: float | None = None,
    ) -> list[Hazard]:
        raw: list[Hazard] = []
        front = self._valid(frame.front_m)
        if self.tf.role == "front" and frame.tfmini_strength >= self.tf.min_strength:
            tf = self._valid(frame.tfmini_m)
            if tf is not None:
                front = tf if front is None else min(front, tf)
        up = self._valid(frame.up_m)

        if front is not None:
            if front <= self.h.stop_distance_m:
                raw.append(Hazard("stop", "stop_now", front))
            elif moving and front <= self.warn_distance("obstacle_front", speed_mps):
                raw.append(Hazard("front", "obstacle_front", front))
        if up is not None and up <= self.h.head_warn_m:
            raw.append(Hazard("head", "obstacle_head", up))
        if moving:
            g = self.ground_check(frame, tilt_deg)
            if g == "drop":
                raw.append(Hazard("drop", "drop_ahead", frame.tfmini_m))
            elif g == "step_up":
                raw.append(Hazard("step_up", "step_up_ahead", frame.tfmini_m))

        seen = {h.kind for h in raw}
        for k in list(self._streak):
            if k not in seen:
                del self._streak[k]
        confirmed = []
        for hz in raw:
            n = self._streak.get(hz.kind, 0) + 1
            self._streak[hz.kind] = n
            # "stop" is immediate; the rest need confirmation
            if hz.kind == "stop" or n >= self.confirm_frames:
                confirmed.append(hz)
        return confirmed
