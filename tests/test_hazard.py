import math

import pytest

from cane.config import HazardCfg, TfminiCfg
from cane.hazard import HazardAssessor, expected_ground_range
from cane.model import SensorFrame


def frame(front=None, up=None, tf=None, gs=900):
    return SensorFrame(t=0.0, front_m=front, up_m=up, tfmini_m=tf, tfmini_strength=gs)


@pytest.fixture
def ha():
    return HazardAssessor(HazardCfg(), TfminiCfg(), confirm_frames=2)


GROUND = expected_ground_range(0.85, TfminiCfg().tilt_down_deg)


def kinds(hs):
    return sorted(h.kind for h in hs)


def test_expected_ground_range():
    assert expected_ground_range(0.85, 35) == pytest.approx(0.85 / math.sin(math.radians(35)))


def test_stop_is_immediate(ha):
    assert kinds(ha.assess(frame(front=0.5, tf=GROUND))) == ["stop"]


def test_front_warning_needs_confirmation(ha):
    # 1.0 m/s * (0.25 + 1.0 + 0.5) + 0.3 = 2.05 m
    assert ha.warn_distance("obstacle_front") == pytest.approx(2.05)
    assert ha.assess(frame(front=1.8, tf=GROUND)) == []
    assert kinds(ha.assess(frame(front=1.8, tf=GROUND))) == ["front"]


def test_streak_resets(ha):
    ha.assess(frame(front=1.8, tf=GROUND))
    ha.assess(frame(front=3.5, tf=GROUND))
    assert ha.assess(frame(front=1.8, tf=GROUND)) == []


def test_longer_clip_increases_warning_distance():
    ha = HazardAssessor(HazardCfg(), TfminiCfg(), clip_s={"obstacle_front": 1.6})
    assert ha.warn_distance("obstacle_front") == pytest.approx(2.65)


def test_head_level(ha):
    ha.assess(frame(up=1.0, tf=GROUND))
    assert kinds(ha.assess(frame(up=1.0, tf=GROUND))) == ["head"]


def test_drop_and_step(ha):
    for _ in range(2):
        out = ha.assess(frame(tf=GROUND + 0.3))
    assert kinds(out) == ["drop"]
    for _ in range(2):
        out = ha.assess(frame(tf=GROUND - 0.3))
    assert kinds(out) == ["step_up"]


def test_weak_tfmini_ignored(ha):
    for _ in range(3):
        assert ha.assess(frame(tf=GROUND + 1.0, gs=10)) == []


def test_imu_tilt_correction(ha):
    # cane held steeper (45 deg): flat ground is closer; with the IMU tilt this is not a step
    flat45 = expected_ground_range(0.85, 45.0)
    for _ in range(2):
        assert ha.assess(frame(tf=flat45), tilt_deg=45.0) == []


def test_invalid_readings(ha):
    for _ in range(3):
        assert ha.assess(frame(front=None, up=9.0)) == []


def test_tfmini_front_role():
    ha = HazardAssessor(HazardCfg(), TfminiCfg(role="front"), confirm_frames=1)
    assert kinds(ha.assess(frame(front=None, tf=0.5))) == ["stop"]


def test_no_front_warning_when_standing(ha):
    for _ in range(3):
        assert ha.assess(frame(front=1.5, tf=GROUND), moving=False) == []
