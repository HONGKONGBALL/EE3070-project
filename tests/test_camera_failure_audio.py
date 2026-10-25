"""Failure announcements survive repeated polls while another clip finishes."""

from pathlib import Path
import threading
from types import SimpleNamespace
import time

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("ncnn")

from cane.vision import demo, warnings


@pytest.mark.parametrize(
    ("failure", "phrase_id", "error"),
    [
        ("camera", "camera_unavailable", "Camera disconnected"),
        ("inference", "visual_unavailable", "Inference unavailable"),
    ],
)
def test_repeated_failure_preserves_queued_announcement(
    tmp_path, monkeypatch, failure, phrase_id, error
):
    clock = SimpleNamespace(now=100.0)
    active_started = threading.Event()
    release_active = threading.Event()
    begin_errors = threading.Event()
    repeated_failure_processed = threading.Event()
    failure_played = threading.Event()
    played = []

    def playback(command, **_kwargs):
        phrase = Path(command[-1]).stem
        played.append(phrase)
        if phrase == "person_ahead":
            active_started.set()
            release_active.wait()
        elif phrase == phrase_id:
            failure_played.set()

    class TestCamera:
        device = "test-camera"

        def __init__(self, *_):
            self.polls = 0
            self.release = threading.Event()

        def start(self):
            pass

        def close(self):
            self.release.set()

        def status(self):
            return {"ready": failure != "camera", "error": error if failure == "camera" else ""}

        def next_frame(self, _previous):
            begin_errors.wait()
            self.polls += 1
            if self.polls > 2:
                # Reaching the next poll proves both error branches completed.
                repeated_failure_processed.set()
                self.release.wait()
                return None
            clock.now = 100.0 + self.polls
            if failure == "camera":
                return None
            return np.zeros((48, 64, 3), np.uint8), self.polls, clock.now

    class TestDetector:
        model_id = "fixture-model"

        def __init__(self, *_):
            pass

        def predict(self, _frame):
            raise RuntimeError(error)

    monkeypatch.setattr(demo, "ROOT", tmp_path)
    monkeypatch.setattr(demo, "Camera", TestCamera)
    monkeypatch.setattr(demo, "Detector", TestDetector)
    monkeypatch.setattr(demo, "time", SimpleNamespace(monotonic=lambda: clock.now, time=time.time))
    monkeypatch.setattr(warnings, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(warnings.subprocess, "run", playback)
    app = demo.Demo({
        "camera": {"device": "test-camera", "width": 64, "height": 48, "fps": 15},
        "model": {"directory": "unused", "size": 416, "confidence": 0.45, "threads": 1},
        "warning": {"roi": [[0, 0], [1, 0], [1, 1], [0, 1]], "cooldown_s": 8},
        "audio": {"directory": "audio/en", "enabled": True},
    })
    try:
        app.start()
        app.audio.submit({"phrase_id": "person_ahead", "ttl_ms": 1500})
        assert active_started.wait(5), "The real Audio worker did not start playback"
        # This obsolete object warning must be cleared when the failure begins.
        app.audio.submit({"phrase_id": "chair_ahead", "ttl_ms": 1500})
        app.jpeg, app.detections = b"old frame", [{"label": "chair"}]
        app.policy.hits["chair"].extend([99.9, 100.0])
        begin_errors.set()
        assert repeated_failure_processed.wait(5), "Demo did not process both identical errors"
        assert app.error == error
        assert app.jpeg is None and app.detections == []
        assert not any(app.policy.hits.values())

        # The active clip ends at 2.2 s; the failure queued at 1 s still has
        # 300 ms of its original TTL after the repeated error at 2 s.
        clock.now = 102.2
        release_active.set()
        assert failure_played.wait(5), f"Queued {phrase_id} never played; played={played}"
        assert played == ["person_ahead", phrase_id]
    finally:
        release_active.set()
        begin_errors.set()
        app.close()
