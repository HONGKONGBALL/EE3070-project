import math
from cane.vision.camera import Camera


def test_status_remains_available_when_capture_timestamps_share_a_clock_tick():
    camera=Camera('test-only-no-reader',640,480,15)
    camera.delivered.extend([100.0,100.0])
    status=camera.status()
    assert status['capture_fps']==0 and math.isfinite(status['capture_fps'])
    camera.delivered.append(100.2)
    assert camera.status()['capture_fps']==10.0
