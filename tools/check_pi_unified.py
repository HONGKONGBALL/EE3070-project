"""Exercise the V2 service with local models, replay inputs and a walking API fixture."""

import argparse
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from unittest.mock import patch

import cv2
from fastapi.testclient import TestClient
from gpiozero import Device
from gpiozero.pins.mock import MockFactory
from piper import SynthesisConfig

from cane.model import OutputRequest, Priority
from cane.nav.amap import AmapClient
from cane.nav.coords import wgs84_to_gcj02
from cane.vision.camera import Camera
from cane.vision.demo import handler_for
from cane.vision.v2 import V2, load_config
from cane.web.app import create_app
from check_wake_local import FixtureMicrophone, TimedSink, TracedSpeech, synthesize_input
from voice_demo import LocalSpeech


ORIGIN = (114.1700, 22.3360)
TURN = (114.1700, 22.3369)
DESTINATION = (114.1706, 22.3369)
CASES = ((ORIGIN, TURN, "右转"), (TURN, DESTINATION, ""))


class CameraFixture(Camera):
    def __init__(self, photo, *args):
        super().__init__(*args)
        self.photo = cv2.imread(str(photo))
        if self.photo is None:
            raise ValueError("Could not load the scene photo")
        self.video = None

    def _run(self):
        current = cap = None
        video_frame = None
        source_frame = 0
        try:
            while not self.stopped.is_set():
                tick = time.monotonic()
                if self.video != current:
                    if cap:
                        cap.release()
                    current = self.video
                    cap = cv2.VideoCapture(str(current)) if current else None
                    started, source_frame, video_frame = tick, 0, None
                    if cap and not cap.isOpened():
                        raise ValueError(f"Cannot open {current}")
                image = self.photo
                if cap:
                    fps = cap.get(cv2.CAP_PROP_FPS) or self.fps
                    target = round((tick - started) * fps)
                    if target >= source_frame:
                        while source_frame < target and cap.grab():
                            source_frame += 1
                        ok, frame = cap.read()
                        if ok:
                            source_frame += 1
                            video_frame = frame
                    if video_frame is not None:
                        image = video_frame
                with self.condition:
                    self.frame = cv2.resize(image, (self.width, self.height))
                    self.sequence += 1
                    self.captured_at = time.monotonic()
                    self.delivered.append(self.captured_at)
                    self.error = ""
                    self.condition.notify_all()
                self.stopped.wait(max(0, 1 / self.fps - (time.monotonic() - tick)))
        finally:
            if cap:
                cap.release()


class SerialFixture:
    def __init__(self):
        self.opened = 0
        self.writes = []
        self.sample = 0
        self.front = 300
        self.buttons = []
        self.closed = threading.Event()

    def readline(self):
        if self.closed.wait(.05):
            return b""
        if self.buttons:
            return self.buttons.pop(0)
        self.sample += 50
        return json.dumps({"t": "d", "f": self.front, "u": 300, "g": 300, "gs": 800, "ms": self.sample}).encode()

    def write(self, data):
        self.writes.append(json.loads(data))

    def close(self):
        self.closed.set()


@contextmanager
def server(handler):
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as http:
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{http.server_port}"
        finally:
            http.shutdown()
            worker.join()


def wait_for(predicate, label, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.025)
    raise AssertionError(f"Timed out waiting for {label}")


def point(coords):
    return ",".join(f"{value:.6f}" for value in coords)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--speech-models", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--replay-seconds", type=float, default=58)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    requests = []

    class MapFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            requests.append(url.path)
            if url.path == "/v3/direction/walking":
                assert query["destination"] == [point(DESTINATION)]
                steps = [{"distance": 75, "polyline": f"{point(a)};{point(b)}", "action": action}
                         for a, b, action in CASES]
                result = {"status": "1", "route": {"paths": [{"distance": 150, "duration": 110, "steps": steps}]}}
            elif url.path == "/v3/place/around":
                result = {"status": "1", "pois": [{"id": "library-1", "name": "CityU Library",
                                                  "location": point(wgs84_to_gcj02(*DESTINATION))}]}
            else:
                self.send_error(404)
                return
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    pico = SerialFixture()

    def open_serial(_):
        pico.opened += 1
        return pico

    nav_config = args.out / "navigation-fixture.toml"
    nav_config.write_text(
        f'[general]\nlog_dir = {json.dumps(args.out.as_posix())}\n'
        '[nav]\ncoord_mode = "none"\n'
        '[[nav.destinations]]\nid = "demo-library"\nname = "CityU Library"\n'
        f"lng = {DESTINATION[0]}\nlat = {DESTINATION[1]}\n"
        '[tfmini]\nrole = "front"\n[imu]\nenabled = false\n',
        encoding="utf-8",
    )
    config = load_config("config/camera.v2.toml", mode="awareness", models_root=args.models_root)
    config["distance"].update(mode="pico", port="local-serial-fixture")
    config["assistant"].update(enabled=True, wake_enabled=False, ptt_gpio=17, request_timeout_s=24,
                               max_scene_age_s=25, speech_models_dir=args.speech_models.as_posix())
    config["navigation"].update(enabled=True, config=nav_config.as_posix())
    Device.pin_factory = pins = MockFactory()
    speech = LocalSpeech(args.speech_models)
    synthesis = SynthesisConfig(noise_scale=0, noise_w_scale=0)
    report = {}

    with ExitStack() as stack:
        base = stack.enter_context(server(MapFixture))
        key = "local-fixture-only"
        stack.enter_context(patch.dict("os.environ", {"AMAP_KEY": key}))
        stack.enter_context(patch("cane.vision.navigation.AmapClient", lambda key: AmapClient(key, base_url=base)))
        stack.enter_context(patch("cane.vision.demo.Camera", lambda *values: CameraFixture(args.image, *values)))
        stack.enter_context(patch("cane.pico_link.SerialPicoLink._open", open_serial))
        app = V2(config)
        navigation = app.navigation
        cane = navigation.controller
        voice = app.assistant
        phone = stack.enter_context(TestClient(create_app(cane, [], key, origin_control=cane, paired_phone=True)))
        microphone = FixtureMicrophone(())
        trace = voice._speech = TracedSpeech(speech)
        voice.microphone = microphone.open
        sink = app.audio.sink = TimedSink(app.audio._record_audio, microphone)
        sink.state, sink.error, sink.last_played, sink.target = "ready", "", None, ""
        base = stack.enter_context(server(handler_for(app, local_dashboard=True)))

        def fetch_status():
            with urlopen(base + "/status", timeout=3) as response:
                return json.load(response)

        def location(point, accuracy=5):
            body = dict(lng=point[0], lat=point[1], accuracy=accuracy)
            assert phone.post("/api/phone/location", json={**body, "token": "bad"}).status_code == 403
            response = phone.post("/api/phone/location", json={**body, "token": key})
            assert response.status_code == 200
            wait_for(lambda: cane.location and cane.location[:3] == (*point, accuracy), "phone fix")

        def command(*utterances, interrupt=False, overlap=False):
            before = len(trace.timeline)
            microphone.utterances = iter([synthesize_input(speech, text, synthesis) for text in utterances])
            wait_for(lambda: not app.audio.busy(), "speaker idle")
            if overlap:
                assert app.audio.arbiter.submit(OutputRequest("go_straight", Priority.NAV))
                wait_for(sink.busy, "route audio to overlap the recording")
                sink.ends = time.monotonic() + 1
            pin = pins.pin(17)
            pin.drive_low()
            assert wait_for(lambda: microphone.active and microphone.utterance_sent.is_set(), "fixture speech capture")
            assert len(trace.timeline) == before
            pin.drive_high()
            if interrupt:
                wait_for(lambda: app.audio.arbiter.current and app.audio.arbiter.current.priority.is_assistant
                         and sink.busy(), "AI playback")
                pico.front = 35
                wait_for(lambda: app.audio.arbiter.current and app.audio.arbiter.current.prompt_id == "stop_now",
                         "Pico proximity warning")
                with app.condition:
                    jpeg = app.jpeg
                if jpeg:
                    (args.out / "preempt-preview.jpg").write_bytes(jpeg)
                pico.front = 300
            wait_for(lambda: voice.status()["state"] == "PASSIVE_LISTENING" and voice.request_id is None, "voice reply")
            assert not voice.status()["error"], voice.status()
            return trace.timeline[before:]

        app.start()
        try:
            wait_for(lambda: fetch_status()["ready"] and navigation.status()["pico_ok"], "camera and Pico telemetry")
            assert not voice.status()["ptt_gpio_unavailable"]
            with urlopen(base + "/assistant/", timeout=3) as response:
                assert b'id="ptt"' in response.read()
            with urlopen(base + "/snapshot.jpg", timeout=3) as response:
                assert response.read(2) == b"\xff\xd8"
            assert app.dashboard_status()["pico"]["front_cm"] == 300
            report["visual"] = command("What's on my left?")
            assert any("sign" in line[1].lower() for line in report["visual"] if line[0] == "tts")
            print("Shared frame, parser and local visual reply passed.", flush=True)
            location(ORIGIN)
            report["cancel"] = command("Navigate to CityU Library.", "No.")
            assert requests == [] and not navigation.status()["active"]
            location(ORIGIN)
            report["plan"] = command("Navigate to CityU Library.", "Yes.")
            wait_for(lambda: navigation.status()["active"], "active route")
            assert requests == ["/v3/direction/walking"]
            wait_for(lambda: any(clips == "nav_start" for clips, _, _ in sink.entries), "route start audio")
            location((ORIGIN[0], 22.33656))
            wait_for(lambda: cane.last_nav is not None, "route maneuver")
            report["next"] = command("What's my next turn?")
            assert requests == ["/v3/direction/walking"]
            assert [row[0] for row in report["next"]] == ["asr"], report["next"]
            assert any(phrase == "nav_stop_align" for phrase, _, _ in sink.entries)
            assert not any(phrase == "turn_right" for phrase, _, _ in sink.entries)
            cane.imu = SimpleNamespace(moving=False, pitch_deg=None)
            pico.buttons.append(b'{"t":"b","id":"ok","ev":"short"}')
            wait_for(lambda: cane.sm.aligned, "stationary orientation confirmation")
            wait_for(lambda: any(phrase == "turn_right" for phrase, _, _ in sink.entries), "route turn after alignment")
            location((ORIGIN[0], 22.33656))
            report["status"] = command("What's my navigation status?")
            assert any("Walking navigation is active" in row[1] for row in report["status"])
            report["overlap"] = command("Stop navigation.", overlap=True)
            assert [row[0] for row in report["overlap"]] == ["tts"], report["overlap"]
            assert "Try asking again" in report["overlap"][0][1]
            assert navigation.status()["active"]
            location((ORIGIN[0], 22.33656))
            report["stop"] = command("Stop navigation.")
            assert not navigation.status()["active"] and "Navigation stopped" in str(report["stop"])
            assert requests == ["/v3/direction/walking"]
            print("Confirmed route, cancellation, next turn gate and stop passed.", flush=True)

            # The paired web app is being exercised in process on Windows.
            navigation.phone_configured = True
            location(ORIGIN)
            report["search"] = command("Navigate to University Library.", "Yes.")
            wait_for(lambda: cane.destination_search.state == "results", "iPhone choice")
            assert requests == ["/v3/direction/walking", "/v3/place/around"]
            headers = {"Authorization": "Bearer " + key}
            choices = phone.get("/api/phone/voice", headers=headers).json()["voice"]
            selection = {"search_id": choices["search_id"], "candidate_id": choices["candidates"][0]["id"]}
            assert phone.post("/api/phone/voice/select", headers=headers, json=selection).json()["accepted"]
            assert not navigation.status()["active"]
            wait_for(lambda: any(phrase == "dest_confirm" for phrase, _, _ in sink.entries),
                     "confirmation audio for a provider place")
            pico.buttons.append(b'{"t":"b","id":"ok","ev":"short"}')
            wait_for(lambda: navigation.status()["active"], "confirmed iPhone candidate")
            assert requests == ["/v3/direction/walking", "/v3/place/around", "/v3/direction/walking"]
            with cane._state_lock:
                cane.location = (*ORIGIN, 5, time.monotonic() - 16)
            wait_for(lambda: not navigation.status()["active"], "navigation ending on stale location")
            wait_for(lambda: any(phrase == "net_lost_nav_off" for phrase, _, _ in sink.entries), "stale location audio")
            print("iPhone search, candidate selection and stale GPS gate passed.", flush=True)

            report["preemption"] = command("What's in front of me?", interrupt=True)
            assert not voice.status()["answer"]
            assert any(event == {"t": "v", "p": "stop"} for event in pico.writes)
            report["speech_interrupted"] = any(row["action"] == "interrupted" for row in app.audio.deliveries)
            assert report["speech_interrupted"]
            app.camera.video = args.video
            wait_for(lambda: all(fetch_status()["tasks"][name]["state"] == "ready"
                                 for name in ("persons", "road", "pothole", "semantic", "puddle")),
                     "YOLO, ground, and people workers", seconds=30)
            video_start = time.monotonic()
            events = {}
            while time.monotonic() - video_start < args.replay_seconds:
                for event in fetch_status()["recent_events"]:
                    if event["created_at"] >= video_start:
                        events[event["event_id"]] = event["phrase_id"]
                time.sleep(.25)
            state = fetch_status()
            report["video_events"] = list(events.values())
            report["tasks"] = {name: {"state": task["state"], "fps": task["fps"]}
                               for name, task in state["tasks"].items() if name != "objects"}
            report["preview_fps"] = state["fps"]
            report["serial_instances"] = pico.opened
            report["heartbeats"] = sum(row["t"] == "h" for row in pico.writes)
            assert pico.opened == 1 and report["heartbeats"] > 3
            report["map_paths"] = requests
            report["audio"] = [phrase for phrase, _, _ in sink.entries]
            report["run_id"] = app.run_id
        finally:
            app.close()
    (args.out / "flow-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
