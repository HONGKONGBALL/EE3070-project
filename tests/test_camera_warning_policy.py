"""Warning streaks and suppression, independent of the camera model runtime."""

import pytest

pytest.importorskip("cv2")
pytest.importorskip("numpy")

from cane.vision.warnings import WarningPolicy


ROI = [[0.25, 0.35], [0.75, 0.35], [0.95, 1], [0.05, 1]]


def detection(label="chair", box=(250, 180, 400, 450)):
    return {"label": label, "confidence": 0.9, "bbox": list(box)}


def test_alternating_detections_and_empty_frames_never_complete_a_streak():
    policy = WarningPolicy(ROI)

    for frame in range(9):
        detections = [detection()] if frame % 2 == 0 else []
        assert policy.update(detections, 640, 480, frame / 8) is None


@pytest.mark.parametrize("missing", [
    [],
    [detection("bottle")],
    [detection(box=(0, 200, 20, 400))],
    [detection(box=(300, 400, 310, 410))],
], ids=["empty", "unsupported-class", "outside-roi", "too-small"])
def test_a_miss_requires_three_fresh_qualifying_observations(missing):
    policy = WarningPolicy(ROI)
    assert policy.update([detection()], 640, 480, 0) is None
    assert policy.update([detection()], 640, 480, 0.125) is None
    assert policy.update(missing, 640, 480, 0.25) is None

    assert policy.update([detection()], 640, 480, 0.375) is None
    assert policy.update([detection()], 640, 480, 0.5) is None
    event = policy.update([detection()], 640, 480, 0.625)
    assert event["object_class"] == "chair"


def test_missing_class_does_not_break_another_class_streak():
    policy = WarningPolicy(ROI)
    for now in (0, 0.125):
        assert policy.update([detection(), detection("person")], 640, 480, now) is None

    event = policy.update([detection("person")], 640, 480, 0.25)
    assert event["object_class"] == "person"


def test_brief_miss_does_not_reannounce_a_continuing_object():
    policy = WarningPolicy(ROI, cooldown=0)
    for now in (0, 0.125):
        assert policy.update([detection()], 640, 480, now) is None
    assert policy.update([detection()], 640, 480, 0.25) is not None
    for now in (0.5, 1, 1.5, 2, 2.5, 3):
        assert policy.update([detection()], 640, 480, now) is None

    assert policy.update([], 640, 480, 3.125) is None
    for now in (3.25, 3.375, 3.5):
        assert policy.update([detection()], 640, 480, now) is None


def test_reappearing_object_still_obeys_its_class_cooldown():
    policy = WarningPolicy(ROI)
    for now in (0, 0.125):
        assert policy.update([detection()], 640, 480, now) is None
    assert policy.update([detection()], 640, 480, 0.25) is not None
    assert policy.update([], 640, 480, 0.375) is None

    for now in (3, 3.125, 3.25, 3.75, 4.25, 4.75, 5.25, 5.75,
                6.25, 6.75, 7.25, 7.75, 8.125):
        assert policy.update([detection()], 640, 480, now) is None
    assert policy.update([detection()], 640, 480, 8.25) is not None


def test_other_class_still_obeys_global_cooldown_after_a_miss():
    policy = WarningPolicy(ROI, cooldown=0)
    for now in (0, 0.125):
        assert policy.update([detection()], 640, 480, now) is None
    assert policy.update([detection()], 640, 480, 0.25) is not None
    assert policy.update([], 640, 480, 0.375) is None

    for now in (0.5, 0.625, 0.75, 1.25, 1.75, 2.25, 2.75, 3.125):
        assert policy.update([detection("person")], 640, 480, now) is None
    event = policy.update([detection("person")], 640, 480, 3.25)
    assert event["object_class"] == "person"


def test_consecutive_observations_must_fit_within_one_second():
    policy = WarningPolicy(ROI)
    for now in (0, 0.625, 1.25):
        assert policy.update([detection()], 640, 480, now) is None

    assert policy.update([detection()], 640, 480, 1.375) is not None
