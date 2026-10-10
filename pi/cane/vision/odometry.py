"""Monocular visual odometry for heading only.

Between consecutive frames: ORB features -> essential matrix -> rotation.
The yaw accumulated since the user last stopped and faced ahead tells us
whether left/right instructions are still valid. If tracking is poor
(few inliers, blur, dark) the heading is "uncertain" and turns are blocked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def yaw_from_rotation(R) -> float:
    """Rotation about the camera's vertical (y) axis, degrees. Positive = turned right."""
    return math.degrees(math.atan2(R[0][2], R[2][2]))


def estimate_rotation(pts1, pts2, K, min_inliers: int = 20):
    """Return (R, inliers) or (None, 0). pts are Nx2 float arrays of matches."""
    import cv2
    import numpy as np

    pts1 = np.asarray(pts1, dtype=np.float64)
    pts2 = np.asarray(pts2, dtype=np.float64)
    if len(pts1) < 8:
        return None, 0
    E, mask = cv2.findEssentialMat(pts1, pts2, K, method=cv2.RANSAC, prob=0.999, threshold=1.0)
    if E is None or E.shape != (3, 3):
        return None, 0
    n, R, _, mask = cv2.recoverPose(E, pts1, pts2, K, mask=mask)
    if n < min_inliers:
        return None, int(n)
    return R, int(n)


@dataclass
class HeadingState:
    yaw_deg: float = 0.0
    tracking: bool = True
    inliers: int = 0


class HeadingTracker:
    def __init__(self, K, max_yaw_deg: float = 15.0, min_inliers: int = 20, lost_frames: int = 5) -> None:
        import cv2

        self.cv2 = cv2
        self.K = K
        self.max_yaw = max_yaw_deg
        self.min_inliers = min_inliers
        self.lost_frames = lost_frames
        self.orb = cv2.ORB_create(800)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        self._prev = None
        self._bad = 0
        self.state = HeadingState()

    def reset_alignment(self) -> None:
        """Call when the user has stopped and faced ahead."""
        self.state = HeadingState()
        self._bad = 0

    def process(self, gray) -> HeadingState:
        kp, des = self.orb.detectAndCompute(gray, None)
        prev, self._prev = self._prev, (kp, des)
        if prev is None or des is None or prev[1] is None:
            return self.state
        matches = self.matcher.match(prev[1], des)
        p1 = [prev[0][m.queryIdx].pt for m in matches]
        p2 = [kp[m.trainIdx].pt for m in matches]
        R, n = estimate_rotation(p1, p2, self.K, self.min_inliers)
        self.state.inliers = n
        if R is None:
            self._bad += 1
            if self._bad >= self.lost_frames:
                self.state.tracking = False
            return self.state
        self._bad = 0
        self.state.yaw_deg += yaw_from_rotation(R.T)  # R maps frame1 -> frame2
        return self.state

    @property
    def orientation_ok(self) -> bool:
        return self.state.tracking and abs(self.state.yaw_deg) <= self.max_yaw


def approx_intrinsics(width: int, height: int, hfov_deg: float = 70.0):
    """Rough K for an uncalibrated webcam."""
    import numpy as np

    f = (width / 2) / math.tan(math.radians(hfov_deg / 2))
    return np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1]], dtype=np.float64)
