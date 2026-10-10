import math
import random

import pytest

from cane.imu import G, ImuSample, MotionDetector, decode_block, pitch_deg
from cane.terrain import (
    FeatureRecorder,
    RoughnessClassifier,
    fit_ground_plane,
    slope_from_plane,
    vibration_features,
)

np = pytest.importorskip("numpy")
FS = 200.0


def window(noise, seed=0, n=200):
    rng = random.Random(seed)
    return [G + rng.gauss(0, noise) for _ in range(n)]


def test_roughness_classes():
    clf = RoughnessClassifier()
    smooth = vibration_features(window(0.8), FS)
    rough = vibration_features(window(3.0), FS)
    assert clf.classify(rough, moving=True) == "rough"
    assert clf.classify(smooth, moving=True) in ("smooth", "soft")
    assert clf.classify(rough, moving=False) == "unknown"


def test_tfmini_jitter_flags_rough():
    f = vibration_features(window(0.8), FS, tf_ranges=[1.40, 1.50, 1.45, 1.58, 1.39])
    assert f.tf_std > 0.04
    assert RoughnessClassifier().classify(f, True) == "rough"


def test_custom_model_hook():
    clf = RoughnessClassifier(model=lambda f: "gravel")
    assert clf.classify(vibration_features(window(1.0), FS), True) == "gravel"


def test_feature_recorder(tmp_path):
    rec = FeatureRecorder(tmp_path / "f.csv")
    f = vibration_features(window(1.0), FS)
    rec.add(f, "brick")
    rec.add(f, "brick")
    lines = (tmp_path / "f.csv").read_text().strip().splitlines()
    assert lines[0].startswith("t,label,rms") and len(lines) == 3


def ground_points(uphill_deg=0.0, left_down_deg=0.0, h=1.0, noise=0.0):
    rng = np.random.default_rng(0)
    xs = rng.uniform(-1, 1, 400)
    zs = rng.uniform(1, 4, 400)
    # camera frame: x right, y down, z forward
    ys = h - zs * math.tan(math.radians(uphill_deg)) - xs * math.tan(math.radians(left_down_deg))  # lower (larger y) on the left
    ys = ys + rng.normal(0, noise, 400)
    return np.stack([xs, ys, zs], axis=1)


@pytest.mark.parametrize(
    "up,left,expected",
    [(0, 0, None), (8, 0, "slope_up"), (-8, 0, "slope_down"), (0, 7, "slope_left"), (0, -7, "slope_right")],
)
def test_slope(up, left, expected):
    plane = fit_ground_plane(ground_points(up, left))
    prompt, uphill, left_down = slope_from_plane(plane)
    assert prompt == expected
    assert uphill == pytest.approx(up, abs=0.5)
    assert left_down == pytest.approx(left, abs=0.5)


def test_slope_compensates_camera_pitch():
    # level floor seen by a camera pitched 20 deg down looks like a slope in camera frame
    p = math.radians(20)
    pts = ground_points()
    rot = np.array([[1, 0, 0], [0, math.cos(p), math.sin(p)], [0, -math.sin(p), math.cos(p)]])
    cam = pts @ rot.T
    prompt, uphill, _ = slope_from_plane(fit_ground_plane(cam), camera_pitch_deg=20)
    assert prompt is None and abs(uphill) < 0.5


def test_plane_residual_reflects_roughness():
    assert fit_ground_plane(ground_points(noise=0.03)).residual_m > fit_ground_plane(ground_points()).residual_m


def test_decode_block_and_pitch():
    # +1 g on Z only, gyro zero
    raw = [0, 0, 0, 0, 0x20, 0x00] + [0, 0] + [0] * 6
    s = decode_block(raw)
    assert s.az == pytest.approx(G) and s.ax == 0 and s.gx == 0
    assert pitch_deg(s) == pytest.approx(0.0)
    tilted = ImuSample(0, -G * math.sin(math.radians(30)), 0, G * math.cos(math.radians(30)), 0, 0, 0)
    assert pitch_deg(tilted) == pytest.approx(30.0)


def test_motion_detector():
    md = MotionDetector(window=40)
    still = [ImuSample(0, 0, 0, G, 0, 0, 0)] * 40
    for s in still:
        md.update(s)
    assert not md.moving
    rng = random.Random(1)
    for _ in range(40):
        md.update(ImuSample(0, 0, 0, G + rng.gauss(0, 2.0), 40, 0, 0))
    assert md.moving
