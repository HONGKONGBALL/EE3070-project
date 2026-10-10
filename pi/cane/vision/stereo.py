"""Stationary stereo depth: steps, kerbs, drops and slope from two USB cameras.

Calibrate first with tools/calibrate_stereo.py (chessboard) -> calib/stereo.npz.
Before use, lock focus/exposure, e.g.
    v4l2-ctl -d /dev/video0 -c focus_automatic_continuous=0 -c auto_exposure=1
(control names differ between cameras; check `v4l2-ctl -l`).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..terrain import GroundPlane, fit_ground_plane, slope_from_plane

log = logging.getLogger(__name__)


class NotCalibrated(RuntimeError):
    pass


class DepthSource(Protocol):
    def points(self):
        """Nx3 numpy array, camera frame (x right, y down, z forward), metres."""


@dataclass(frozen=True)
class ScanResult:
    plane: GroundPlane | None
    slope_prompt: str | None
    uphill_deg: float
    left_down_deg: float
    step_prompt: str | None      # step_up_ahead | drop_ahead | None
    step_distance_m: float | None
    rough: bool

    @property
    def prompts(self) -> list[str]:
        out = []
        if self.step_prompt:
            out.append(self.step_prompt)
        if self.slope_prompt:
            out.append(self.slope_prompt)
        if self.rough:
            out.append("rough_ground")
        return out


def analyze_points(points, camera_pitch_deg: float = 0.0, near=(0.5, 1.3), far_max=3.0,
                   corridor_m=0.4, bin_m=0.25, step_m=0.08, rough_resid_m=0.025,
                   slope_threshold_deg: float = 4.0) -> ScanResult:
    """Fit the ground right in front, then walk forward along the walking corridor.

    A bin whose median height sits more than `step_m` above the near-ground
    plane is a step up / kerb; more than `step_m` below is a drop. Missing ground
    in a bin after valid bins is treated as a possible drop.
    """
    import numpy as np

    pts = np.asarray(points, dtype=float)
    pts = pts[np.isfinite(pts).all(axis=1)]
    corridor = pts[np.abs(pts[:, 0]) <= corridor_m]
    near_pts = corridor[(corridor[:, 2] >= near[0]) & (corridor[:, 2] <= near[1])]
    if len(near_pts) < 30:
        return ScanResult(None, None, 0.0, 0.0, None, None, False)
    plane = fit_ground_plane(near_pts)
    slope_prompt, uphill, left_down = slope_from_plane(plane, camera_pitch_deg, slope_threshold_deg)

    n = np.array(plane.normal)
    c = near_pts.mean(axis=0)
    step_prompt, step_dist = None, None
    seen_ground = False
    z = near[1]
    while z < far_max:
        sel = corridor[(corridor[:, 2] >= z) & (corridor[:, 2] < z + bin_m)]
        if len(sel) < 10:
            if seen_ground:
                step_prompt, step_dist = "drop_ahead", z
                break
            z += bin_m
            continue
        seen_ground = True
        h = float(np.median((sel - c) @ n))  # height above the near-ground plane
        if h > step_m:
            step_prompt, step_dist = "step_up_ahead", z
            break
        if h < -step_m:
            step_prompt, step_dist = "drop_ahead", z
            break
        z += bin_m
    return ScanResult(plane, slope_prompt, uphill, left_down, step_prompt, step_dist,
                      plane.residual_m >= rough_resid_m)


class StereoCalib:
    def __init__(self, path: str | Path) -> None:
        import cv2
        import numpy as np

        path = Path(path)
        if not path.exists():
            raise NotCalibrated(f"{path} missing - run tools/calibrate_stereo.py")
        d = np.load(path)
        self.size = tuple(int(v) for v in d["size"])
        R1, R2, P1, P2, self.Q, _, _ = cv2.stereoRectify(d["K1"], d["D1"], d["K2"], d["D2"], self.size, d["R"], d["T"])
        self.map_l = cv2.initUndistortRectifyMap(d["K1"], d["D1"], R1, P1, self.size, cv2.CV_16SC2)
        self.map_r = cv2.initUndistortRectifyMap(d["K2"], d["D2"], R2, P2, self.size, cv2.CV_16SC2)
        self.K1 = d["K1"]


class StereoDepth:
    """DepthSource from two USB cameras. Only call while stationary."""

    def __init__(self, calib: StereoCalib, left_device: str, right_device: str) -> None:
        import cv2

        self.cv2 = cv2
        self.calib = calib
        self.left = cv2.VideoCapture(left_device)
        self.right = cv2.VideoCapture(right_device)
        w, h = calib.size
        for cap in (self.left, self.right):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.sgbm = cv2.StereoSGBM_create(minDisparity=0, numDisparities=96, blockSize=7,
                                          P1=8 * 49, P2=32 * 49, uniquenessRatio=10,
                                          speckleWindowSize=100, speckleRange=2)

    def capture_pair(self):
        # flush stale buffered frames; cameras are not synchronised
        for _ in range(2):
            self.left.grab()
            self.right.grab()
        ok1, l = self.left.retrieve()
        ok2, r = self.right.retrieve()
        if not (ok1 and ok2):
            raise RuntimeError("camera read failed")
        return l, r

    def points(self):
        cv2 = self.cv2
        l, r = self.capture_pair()
        gl = cv2.remap(cv2.cvtColor(l, cv2.COLOR_BGR2GRAY), *self.calib.map_l, cv2.INTER_LINEAR)
        gr = cv2.remap(cv2.cvtColor(r, cv2.COLOR_BGR2GRAY), *self.calib.map_r, cv2.INTER_LINEAR)
        disp = self.sgbm.compute(gl, gr).astype("float32") / 16.0
        pts = cv2.reprojectImageTo3D(disp, self.calib.Q)
        mask = disp > 1.0
        h = pts.shape[0]
        mask[: h // 2, :] = False  # ground is in the lower half
        return pts[mask].reshape(-1, 3)

    def close(self) -> None:
        self.left.release()
        self.right.release()


def scan(source: DepthSource, camera_pitch_deg: float = 0.0) -> ScanResult:
    t0 = time.monotonic()
    res = analyze_points(source.points(), camera_pitch_deg)
    log.info("stereo scan %.0f ms: %s", (time.monotonic() - t0) * 1000, res.prompts)
    return res
