"""Ground classification WITHOUT the lower ToF node.

The original plan used a VL53L8CX + IMU near the tip. That node is not built
this term, so roughness and slope come from:

1. Cane-shaft vibration (MPU6050) while the roller tip rolls: RMS / peak /
   zero-crossing / high-band energy over ~1 s windows.
2. TFmini-S range jitter when it looks down at the ground ahead.
3. Ground-plane fit on stereo depth when the user is stationary (slope + residual).

Limitation (must be stated in the report): small holes or drops within ~0.4 m
of the tip are only felt when the tip hits them (IMU shock), i.e. after the
fact, not in advance. The thresholds here are placeholders until the group
collects and labels data on 2-3 surfaces; `FeatureRecorder` writes that data
and `RoughnessClassifier.model` can be swapped for a trained model.
"""

from __future__ import annotations

import csv
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class VibrationFeatures:
    rms: float          # of high-passed |a|, m/s^2
    peak: float         # max |high-passed |a||
    zcr: float          # zero crossings per second
    hf_ratio: float     # energy above 15 Hz / total
    tf_std: float = 0.0  # TFmini range std over the window (m), 0 if unused


def _highpass(x: Sequence[float], alpha: float = 0.9) -> list[float]:
    out, prev_x, prev_y = [], x[0], 0.0
    for v in x:
        y = alpha * (prev_y + v - prev_x)
        out.append(y)
        prev_x, prev_y = v, y
    return out


def _hf_ratio(x: Sequence[float], fs: float, cutoff: float = 15.0) -> float:
    n = len(x)
    total = hf = 0.0
    # naive DFT on a short window; numpy not required
    for k in range(1, n // 2):
        re = sum(x[i] * math.cos(2 * math.pi * k * i / n) for i in range(n))
        im = sum(x[i] * math.sin(2 * math.pi * k * i / n) for i in range(n))
        p = re * re + im * im
        total += p
        if k * fs / n >= cutoff:
            hf += p
    return hf / total if total > 0 else 0.0


def vibration_features(accel_norm: Sequence[float], fs: float,
                       tf_ranges: Sequence[float] | None = None) -> VibrationFeatures:
    if len(accel_norm) < 8:
        raise ValueError("window too short")
    hp = _highpass(accel_norm)
    rms = math.sqrt(sum(v * v for v in hp) / len(hp))
    peak = max(abs(v) for v in hp)
    zc = sum(1 for a, b in zip(hp, hp[1:]) if (a < 0) != (b < 0))
    zcr = zc * fs / len(hp)
    # decimate for the DFT so a 200-sample window stays cheap
    step = max(1, len(hp) // 64)
    hf = _hf_ratio(hp[::step], fs / step)
    tf_std = 0.0
    if tf_ranges and len(tf_ranges) > 1:
        m = sum(tf_ranges) / len(tf_ranges)
        tf_std = math.sqrt(sum((r - m) ** 2 for r in tf_ranges) / len(tf_ranges))
    return VibrationFeatures(rms, peak, zcr, hf, tf_std)


class RoughnessClassifier:
    """smooth | rough | soft | unknown. Only meaningful while walking."""

    def __init__(self, rough_rms: float = 1.5, soft_rms: float = 0.25, rough_tf_std: float = 0.04,
                 model: Callable[[VibrationFeatures], str] | None = None) -> None:
        self.rough_rms = rough_rms
        self.soft_rms = soft_rms
        self.rough_tf_std = rough_tf_std
        self.model = model

    def classify(self, f: VibrationFeatures, moving: bool) -> str:
        if not moving:
            return "unknown"
        if self.model is not None:
            return self.model(f)
        if f.rms >= self.rough_rms or f.tf_std >= self.rough_tf_std:
            return "rough"
        if f.rms <= self.soft_rms and f.hf_ratio < 0.2:
            # a rolling tip on grass/soft ground is strongly damped
            return "soft"
        return "smooth"


class FeatureRecorder:
    """Append labelled feature rows to CSV for training (data collection mode)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, f: VibrationFeatures, label: str) -> None:
        new = not self.path.exists()
        with self.path.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["t", "label", *asdict(f).keys()])
            if new:
                w.writeheader()
            w.writerow({"t": round(time.time(), 3), "label": label, **asdict(f)})


# ----------------------------------------------------------------- slope ---
@dataclass(frozen=True)
class GroundPlane:
    normal: tuple[float, float, float]  # unit normal, camera frame (x right, y down, z forward)
    residual_m: float                   # RMS distance of inliers to the plane


def fit_ground_plane(points) -> GroundPlane:
    """Least-squares plane through Nx3 points (numpy array) in camera frame."""
    import numpy as np

    pts = np.asarray(points, dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 3 or len(pts) < 3:
        raise ValueError("need Nx3 points, N >= 3")
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c)
    n = vt[-1]
    if n[1] > 0:  # make the normal point up (camera y is down)
        n = -n
    resid = float(np.sqrt(np.mean(((pts - c) @ n) ** 2)))
    return GroundPlane((float(n[0]), float(n[1]), float(n[2])), resid)


def slope_from_plane(plane: GroundPlane, camera_pitch_deg: float = 0.0,
                     threshold_deg: float = 4.0) -> tuple[str | None, float, float]:
    """Return (prompt_id or None, uphill_deg, left_down_deg).

    uphill_deg > 0: ground rises ahead. left_down_deg > 0: ground is lower on
    the left (tilts left).

    camera_pitch_deg: camera tilt below horizontal (from the IMU); it is removed
    so a level floor reads 0 deg.
    """
    nx, ny, nz = plane.normal
    p = math.radians(camera_pitch_deg)
    # rotate the normal from camera frame into a level frame (about x axis)
    ny_l = ny * math.cos(p) - nz * math.sin(p)
    nz_l = ny * math.sin(p) + nz * math.cos(p)
    up = -ny_l
    uphill = math.degrees(math.atan2(-nz_l, up))   # normal tilting back = ground rises ahead
    left_down = math.degrees(math.atan2(-nx, up))  # normal leaning left = ground lower on the left
    prompt = None
    if abs(uphill) >= abs(left_down) and abs(uphill) >= threshold_deg:
        prompt = "slope_up" if uphill > 0 else "slope_down"
    elif abs(left_down) >= threshold_deg:
        prompt = "slope_left" if left_down > 0 else "slope_right"
    return prompt, uphill, left_down
