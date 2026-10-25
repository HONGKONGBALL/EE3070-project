"""Controlled Pi-only checks; restores the camera configuration afterwards."""

import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
UNIT = "smart-cane-camera"


def get_status():
    with urllib.request.urlopen(BASE + "/status", timeout=2) as response:
        return json.load(response)


def wait_for(predicate):
    deadline = time.monotonic() + 15
    last = None
    while time.monotonic() < deadline:
        try:
            last = get_status()
            if predicate(last):
                return last
        except OSError:
            pass
        time.sleep(0.15)
    raise RuntimeError(f"State check timed out: {last}")


def restart():
    subprocess.run(["systemctl", "--user", "restart", UNIT], check=True)


def assert_no_frame():
    for request in (BASE + "/frame.jpg", urllib.request.Request(BASE + "/capture", data=b"", method="POST")):
        try:
            urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
        else:
            raise AssertionError("Unavailable camera served an image or accepted a capture")


def main():
    root = Path(__file__).resolve().parents[1]
    config = root / "config/camera.example.toml"
    original = config.read_text()
    (root / "config/camera.before-validation.local.toml").write_text(original)
    report = {"started_at": time.time(), "restarts": []}
    try:
        for trial in range(3):
            restart()
            status = wait_for(lambda s: s["ready"])
            time.sleep(0.3)
            later = get_status()
            assert later["frame_id"] > status["frame_id"]
            report["restarts"].append({"run_id": status["run_id"], "first_frame": status["frame_id"],
                                        "later_frame": later["frame_id"]})
            print(f"Restart {trial + 1}: ready, frames advancing", flush=True)

        config.write_text(original.replace('device = "auto"', 'device = "/dev/cane-validation-missing"'))
        restart()
        bad = wait_for(lambda s: s.get("model_state") == "ready" and "opened" in s["camera"]["error"])
        assert not bad["ready"] and not bad["detections"]
        assert_no_frame()
        report["camera_unavailable"] = {"passed": True, "error": bad["camera"]["error"]}
        print("Unavailable device: error state and HTTP 503 verified", flush=True)

        config.write_text(original.replace('directory = "models/yolo11n"', 'directory = "models/cane-validation-missing"'))
        restart()
        bad = wait_for(lambda s: s.get("model_state") == "error")
        assert not bad["ready"] and "Model unavailable" in bad["error"]
        assert_no_frame()
        report["model_unavailable"] = {"passed": True, "error": bad["error"]}
        print("Missing weights: visible error state and HTTP 503 verified", flush=True)
    finally:
        config.write_text(original)
        restart()
        status = wait_for(lambda s: s["ready"])
        report["restored"] = {"ready": status["ready"], "run_id": status["run_id"]}
        report["finished_at"] = time.time()
        output = root / "logs/camera-recovery.json"
        output.write_text(json.dumps(report, indent=2))
        print("Original configuration restored and live camera ready", flush=True)


if __name__ == "__main__":
    main()
