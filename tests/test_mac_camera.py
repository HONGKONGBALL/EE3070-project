"""Platform selection does not open a real webcam in tests."""

import cv2


def test_mac_camera_uses_numeric_avfoundation_source() -> None:
    from cane.vision.camera import capture_source

    assert capture_source("0", "Darwin") == (0, cv2.CAP_AVFOUNDATION)


def test_linux_camera_keeps_v4l2_device() -> None:
    from cane.vision.camera import capture_source

    assert capture_source("/dev/video2", "Linux") == ("/dev/video2", cv2.CAP_V4L2)
