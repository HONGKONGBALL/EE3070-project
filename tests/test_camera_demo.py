"""Camera policy and exported-model decoding, without physical hardware."""

import numpy as np
import pytest
from http.server import ThreadingHTTPServer
import json
import io
from pathlib import Path
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request
import time

cv2 = pytest.importorskip("cv2")
pytest.importorskip("ncnn")

from cane.vision.detector import decode
from cane.vision.warnings import WarningPolicy


def detection(label="chair", box=(250, 180, 400, 450)):
    return {"label": label, "confidence": 0.9, "bbox": list(box)}


ROI = [[0.25, 0.35], [0.75, 0.35], [0.95, 1], [0.05, 1]]


def test_warning_requires_independent_observations_and_respects_cooldown():
    policy = WarningPolicy(ROI)
    assert policy.update([detection()], 640, 480, 0) is None
    assert policy.update([detection()], 640, 480, 0.1) is None
    event = policy.update([detection()], 640, 480, 0.2)
    assert event["object_class"] == "chair"
    assert event["distance_m"] is None
    assert event["text"] == "Chair ahead. Please be careful."
    for t in np.arange(0.3, 8.1, 0.1):
        assert policy.update([detection()], 640, 480, float(t)) is None
    assert policy.update([detection()], 640, 480, 8.3) is None
    policy.update([], 640, 480, 10.5)
    assert policy.update([detection()], 640, 480, 10.6) is None
    assert policy.update([detection()], 640, 480, 10.7) is None
    assert policy.update([detection()], 640, 480, 10.8) is not None


def test_side_target_and_unknown_class_do_not_generate_ahead_warning():
    policy = WarningPolicy(ROI)
    for t in (0, 0.1, 0.2, 1.0):
        left = detection(box=(0, 200, 20, 400))
        bottle = detection("bottle")
        assert policy.update([left, bottle], 640, 480, t) is None
        assert not left["ahead"] and not bottle["ahead"]


def test_expired_hits_do_not_complete_a_warning_after_camera_failure():
    policy = WarningPolicy(ROI)
    policy.update([detection()], 640, 480, 0)
    policy.update([detection()], 640, 480, 0.1)
    policy.reset()
    assert policy.update([detection()], 640, 480, 0.2) is None


def test_decoder_maps_letterboxed_coordinates_and_keeps_different_classes():
    # 32x32 input has 16+4+1 grid cells; original 64x48 was padded by 4 rows.
    prediction = np.full((21, 144), -20, dtype=np.float32)
    for row, label in ((5, 56), (6, 56), (9, 24)):
        prediction[row, :64] = -10
        prediction[row, [2, 18, 34, 50]] = 10
        prediction[row, 64 + label] = 8
    result = decode(prediction, 32, 32, 0.5, 0, 4, 64, 48, 0.4)
    assert {d["label"] for d in result} == {"chair", "backpack"}
    assert all(0 <= d["bbox"][0] <= d["bbox"][2] <= 63 for d in result)
    assert all(0 <= d["bbox"][1] <= d["bbox"][3] <= 47 for d in result)
    assert all(d["confidence"] > 0.99 for d in result)


def test_decoder_rejects_a_different_export_instead_of_silent_wrong_boxes():
    with pytest.raises(ValueError, match="output shape"):
        decode(np.zeros((21, 84)), 32, 32, 1, 0, 0, 32, 32, 0.4)


def test_http_serves_fresh_jpeg_and_saves_actual_capture(tmp_path, monkeypatch):
    from cane.vision import demo
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    image = cv2.imencode(".jpg", np.zeros((48, 64, 3), dtype=np.uint8))[1].tobytes()
    sample = SimpleNamespace(image=lambda: image)
    server = ThreadingHTTPServer(("127.0.0.1", 0), demo.handler_for(sample))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/frame.jpg") as response:
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.read() == image
        request = urllib.request.Request(base + "/capture", data=b"", method="POST")
        result = json.load(urllib.request.urlopen(request))
        assert Path(result["path"]).read_bytes() == image
        assert result["bytes"] == len(image)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_http_does_not_return_or_capture_a_stale_image(tmp_path, monkeypatch):
    from cane.vision import demo
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    sample = SimpleNamespace(image=lambda: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), demo.handler_for(sample))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for request in (base + "/frame.jpg",
                        urllib.request.Request(base + "/capture", data=b"", method="POST")):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            assert error.value.code == 503
        assert not (tmp_path / "captures").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_warning_log_keeps_event_kind_without_colliding_with_log_category():
    from cane.vision.demo import Demo
    app = Demo.__new__(Demo)
    app.log_lock, app.logfile = threading.Lock(), io.StringIO()
    app.log("warning", kind="OBJECT_AHEAD", object_class="chair")
    record = json.loads(app.logfile.getvalue())
    assert record["record_type"] == "warning"
    assert record["kind"] == "OBJECT_AHEAD"


def test_camera_worker_generates_and_submits_warning_end_to_end(tmp_path, monkeypatch):
    from cane.vision import demo

    class TestCamera:
        device = "test-camera"
        sequence = 0
        def __init__(self, *_): pass
        def start(self): pass
        def close(self): pass
        def status(self): return {"ready": True, "error": ""}
        def next_frame(self, _):
            time.sleep(0.01)
            if self.sequence >= 3:
                return None
            self.sequence += 1
            return np.zeros((480, 640, 3), np.uint8), self.sequence, time.monotonic()

    class TestDetector:
        model_id = "fixture-model"
        def __init__(self, *_): pass
        def predict(self, _): return [detection()]

    class TestAudio:
        state, error, last_played = "disabled", "", None
        def __init__(self, *_): self.submitted = []
        def submit(self, event): self.submitted.append(event)
        def clear_pending(self): pass
        def close(self): pass

    monkeypatch.setattr(demo, "ROOT", tmp_path)
    monkeypatch.setattr(demo, "Camera", TestCamera)
    monkeypatch.setattr(demo, "Detector", TestDetector)
    monkeypatch.setattr(demo, "Audio", TestAudio)
    config = {
        "camera": {"device": "test-camera", "width": 640, "height": 480, "fps": 15},
        "model": {"directory": "unused", "size": 416, "confidence": 0.45, "threads": 1},
        "warning": {"roi": ROI, "cooldown_s": 8},
        "audio": {"directory": "audio/en", "enabled": False},
    }
    app = demo.Demo(config)
    app.start()
    try:
        deadline = time.monotonic() + 2
        while not app.events and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(app.events) == 1
        event = app.events[0]
        assert event["object_class"] == "chair"
        assert event["frame_id"] == 3
        assert event["distance_m"] is None
        assert event in app.audio.submitted
        records = [json.loads(s) for s in app.log_path.read_text().splitlines()]
        assert any(r.get("kind") == "OBJECT_AHEAD" for r in records)
    finally:
        app.close()
