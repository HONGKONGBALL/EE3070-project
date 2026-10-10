import math

import pytest

np = pytest.importorskip("numpy")

from cane.vision.stereo import analyze_points  # noqa: E402
from cane.vision.odometry import approx_intrinsics, estimate_rotation, yaw_from_rotation  # noqa: E402


def ground(z0=0.4, z1=3.0, h=0.9, n=4000, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.uniform(-0.6, 0.6, n)
    z = rng.uniform(z0, z1, n)
    return np.stack([x, np.full(n, h), z], axis=1)


def test_flat_ground_is_clear():
    r = analyze_points(ground())
    assert r.step_prompt is None and r.slope_prompt is None and not r.rough


def test_step_up_detected():
    pts = ground()
    pts[pts[:, 2] > 2.0, 1] -= 0.15  # 15 cm higher beyond 2 m
    r = analyze_points(pts)
    assert r.step_prompt == "step_up_ahead"
    assert 1.75 <= r.step_distance_m <= 2.1


def test_drop_detected():
    pts = ground()
    pts[pts[:, 2] > 1.8, 1] += 0.2
    assert analyze_points(pts).step_prompt == "drop_ahead"


def test_missing_ground_is_drop():
    pts = ground()
    pts = pts[pts[:, 2] < 1.9]
    assert analyze_points(pts, far_max=2.5).step_prompt == "drop_ahead"


def test_too_few_points():
    r = analyze_points(np.zeros((5, 3)))
    assert r.plane is None and r.prompts == []


def rot_y(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])


def test_yaw_from_rotation():
    assert yaw_from_rotation(rot_y(10)) == pytest.approx(10)


def test_estimate_rotation_synthetic():
    pytest.importorskip("cv2")
    K = approx_intrinsics(640, 480)
    rng = np.random.default_rng(1)
    P = np.stack([rng.uniform(-3, 3, 300), rng.uniform(-1, 1, 300), rng.uniform(4, 12, 300)], axis=1)

    def project(Pc):
        uv = (K @ Pc.T).T
        return uv[:, :2] / uv[:, 2:]

    # second camera turned 8 deg to the right and stepped 0.3 m forward
    R = rot_y(8).T
    t = np.array([0, 0, -0.3])
    P2 = (R @ P.T).T + t
    Rest, n = estimate_rotation(project(P), project(P2), K)
    assert Rest is not None and n > 100
    assert yaw_from_rotation(Rest.T) == pytest.approx(8, abs=0.5)
