"""Standalone live camera sample. No Pico, stereo calibration or navigation."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import threading
import time
import tomllib
import uuid

import cv2
import numpy as np

from .camera import Camera
from .detector import Detector
from .warnings import Audio, PHRASES, WarningPolicy

ROOT = Path(__file__).resolve().parents[3]
PAGE = b"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>Pi Camera - Live Detection</title>
<style>body{margin:0;background:#111;color:#ddd;font:15px sans-serif;text-align:center}
img{display:block;max-width:100%;max-height:90vh;margin:auto}p{margin:10px}a{color:#9ddcff}</style>
<img id=video alt="Waiting for live camera"><p><span id=status>Starting...</span>
 &nbsp; <a href=/snapshot.jpg target=_blank>Snapshot</a></p>
<details id=audioControls hidden><summary>Headphones / audio output</summary>
<p>Pair Bluetooth headphones with the Pi, or plug a USB receiver into the Pi.</p>
<button id=findOutputs>Refresh headphones</button>
<select id=audioOutput aria-label="Audio output"></select>
<label><input id=audioEnabled type=checkbox>Voice alerts</label>
<button id=saveOutput>Save output</button><button id=testOutput>Test voice</button>
<p id=audioMessage role=status></p></details>
<details id=diagnostics hidden><summary>Ground detection details</summary>
<p>Counts show the latest observation, not accuracy. A handled alert does not confirm audible playback.</p>
<p id=episodes></p>
<p id=crowdStatus></p>
<table style="margin:10px auto;text-align:left;border-spacing:18px 6px">
<thead><tr><th>Task</th><th>Candidates</th><th>Result</th></tr></thead><tbody id=ground></tbody></table></details>
<script>
const video=document.getElementById('video'),status=document.getElementById('status');
const details=document.getElementById('diagnostics'),ground=document.getElementById('ground');
const reasons={outside_path:'Outside walking area',verifier_rejected:'Appearance check rejected',
 model_filtered:'Filtered by model',context_rejected:'Ground context rejected',
 ground_context_unavailable:'Waiting for ground context',awaiting_semantic_pair:'Waiting for matching stair view',
 awaiting_confirmation:'Accumulating independent evidence',episode_announced:'Episode already announced / audio disabled',episode_pending_audio:'Fresh alert pending delivery',episode_retry_wait:'Waiting for fresh evidence to retry',already_handled:'Already handled',cooldown:'Repeat paused',
 eligible:'Ready for alert decision',unsupported_label:'Unsupported category'};
Object.assign(reasons,{context_only:'Context label only',other_task_owns_category:'Handled by dedicated detector',
 indoor_context_rejected:'Indoor floor context',strong_pothole_evidence_required:'Stronger pothole evidence required'});
let attached=false;
let audioLoaded=false;
const audioControls=document.getElementById('audioControls'),output=document.getElementById('audioOutput');
const audioEnabled=document.getElementById('audioEnabled'),audioMessage=document.getElementById('audioMessage');
async function audioRequest(path,body){
 const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
 const response=await fetch(path,options),result=await response.json();
 if(!response.ok)throw new Error(result.error||'Audio request failed');
 return result;
}
async function loadOutputs(){
 try{const result=await audioRequest('/audio-outputs');output.replaceChildren();
 for(const device of result.outputs){const option=document.createElement('option');option.value=device.target;option.textContent=`${device.name} (${device.transport})`;output.append(option)}
 if(result.target)output.value=result.target;
 audioEnabled.checked=result.enabled;
 audioMessage.textContent=result.outputs.length?'Select headphones, save, then test.':'No headphones connected. Pair AirPods or plug in a USB receiver, then refresh.';
 }catch(error){audioMessage.textContent=error.message;}
}
audioControls.addEventListener('toggle',()=>{if(audioControls.open&&!audioLoaded){audioLoaded=true;loadOutputs()}});
document.getElementById('findOutputs').onclick=loadOutputs;
document.getElementById('saveOutput').onclick=async()=>{
 try{await audioRequest('/audio-output',{enabled:audioEnabled.checked,target:output.value});audioMessage.textContent='Saved. Test voice to check the selected headphones.'}
 catch(error){audioMessage.textContent=error.message;}
};
document.getElementById('testOutput').onclick=async()=>{
 try{const result=await audioRequest('/test-audio',{});audioMessage.textContent=result.state==='disabled'?'Voice alerts are off. Enable and save first.':'Test requested. Confirm that you hear it in your headphones.'}
 catch(error){audioMessage.textContent=error.message;}
};
video.onerror=()=>{attached=false;video.removeAttribute('src')};
async function refresh(){
 try{const s=await(await fetch('/status',{cache:'no-store'})).json();
 status.textContent=s.ready?`${s.fps} FPS | ${s.inference_ms} ms inference | Audio: ${s.audio.state}`:s.error;
 if(s.version===2&&s.ready){
  const tasks=Object.entries(s.tasks).filter(([,v])=>v.state!=='disabled').map(([k,v])=>`${k}: ${v.state==='ready'?v.fps+' Hz':v.state+' '+v.error}`).join(' | ');
  const mode=s.vision_mode==='awareness'?'Ground + crowd awareness':s.vision_mode==='ground'?'Ground mode':'Combined mode';
  status.textContent=`${mode} | ${s.input_source} | ${s.fps} preview FPS | ${tasks} | Audio: ${s.audio.state}`;
 }
 details.hidden=s.version!==2;
 audioControls.hidden=s.version!==2;
 if(details.open){
  const rows=Object.entries(s.ground_diagnostics||{}).map(([task,d])=>{
   const row=document.createElement('tr');
   const result=d.state!=='observed'?'No usable recent result':d.raw_count===0?'No candidates detected':
    Object.entries(d.reason_counts||{}).map(([reason,n])=>`${reasons[reason]||reason}: ${n}`).join('; ');
   for(const value of [task,d.raw_count??'-',result]){const cell=document.createElement('td');cell.textContent=value;row.appendChild(cell)}
   return row;
  });ground.replaceChildren(...rows);
  document.getElementById('episodes').textContent=Object.entries(s.hazard_episodes||{}).map(([name,e])=>`${name}: ${e.state}, delivery ${e.delivery_status}`).join(' | ');
  const crowd=s.crowd;
  document.getElementById('crowdStatus').textContent=crowd?.enabled?`Crowd: ${crowd.state}. ${crowd.evidence?.person_count??0} people seen; largest group ${crowd.evidence?.cluster_count??0}. ${crowd.reason}. Analysis ${crowd.processing_ms} ms.`:'';
 }
 video.style.visibility=s.ready?'visible':'hidden';
 if(s.ready&&!attached){video.src='/stream?t='+Date.now();attached=true;}
 }catch(e){status.textContent='Connection lost';video.style.visibility='hidden';}
 setTimeout(refresh,700);
}refresh();
</script>"""


class Demo:
    def __init__(self, config, audio_factory=None, camera_factory=None):
        cam, model, audio = config["camera"], config["model"], config["audio"]
        device = cam["device"]
        if device == "auto":
            devices = sorted(glob.glob("/dev/v4l/by-id/*video-index0"))
            device = devices[0] if devices else "/dev/video0"
        cv2.setNumThreads(1)
        self.camera = (camera_factory or Camera)(device, cam["width"], cam["height"], cam["fps"])
        self.model_config = model
        self.frame_period = 1 / model.get("max_fps", 8)
        self.detector = None
        self.model_state = "loading"
        self.policy = WarningPolicy(config["warning"]["roi"], config["warning"]["cooldown_s"])
        self.run_id = uuid.uuid4().hex[:10]
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.jpeg = None
        self.sequence = 0
        self.frame_id = 0
        self.captured_at = 0.0
        self.error = "Loading detector"
        self.detections = []
        self.events = deque(maxlen=30)
        self.timings = deque(maxlen=1200)
        self.completed = deque(maxlen=100)
        self.inference_ms = 0.0
        self.current_text = ""
        self.log_path = ROOT / "logs" / f"camera-{self.run_id}.jsonl"
        self.log_path.parent.mkdir(exist_ok=True)
        self.logfile = self.log_path.open("a", buffering=1)
        self.log_lock = threading.Lock()
        self.audio = (audio_factory or Audio)(ROOT / audio["directory"], audio["enabled"], audio.get("target", ""), self.log)
        self.thread = threading.Thread(target=self._run, daemon=True, name="detect")

    def log(self, record_type, **fields):
        with self.log_lock:
            self.logfile.write(json.dumps({"time": time.time(), "record_type": record_type, **fields}) + "\n")

    def start(self):
        self.log("start", model=Detector.model_id, device=self.camera.device)
        self.camera.start()
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.camera.close()
        with self.condition:
            self.condition.notify_all()
        self.thread.join(timeout=5)
        self.audio.close()
        self.log("stop")
        self.logfile.close()

    def _run(self):
        try:
            model = self.model_config
            self.detector = Detector(ROOT / model["directory"], model["size"],
                                     model["confidence"], model["threads"])
            self.model_state = "ready"
        except Exception as exc:
            self.model_state, self.error = "error", f"Model unavailable: {exc}"
            self.log("model_error", message=self.error)
            self.audio.submit({"phrase_id": "visual_unavailable", "ttl_ms": 1500})
            self.camera.close()
            return
        previous = 0
        while not self.stopped.is_set():
            sample = self.camera.next_frame(previous)
            if sample is None:
                self.policy.reset()
                message = self.camera.status()["error"] or "No fresh camera frame"
                changed = message != self.error
                with self.condition:
                    self.jpeg, self.detections = None, []
                    self.error = message
                if changed:
                    self.audio.clear_pending()
                    self.log("camera_error", message=message)
                    self.audio.submit({"phrase_id": "camera_unavailable", "ttl_ms": 1500})
                continue
            frame, frame_id, captured = sample
            previous = frame_id
            try:
                started = time.monotonic()
                detections = self.detector.predict(frame)
                inferred = time.monotonic()
                if inferred - captured > 1.5:
                    raise RuntimeError("Inference result is stale")
                h, w = frame.shape[:2]
                event = self.policy.update(detections, w, h, inferred)
                current = next((d for d in detections if d.get("ahead")), None)
                text = ""
                if current and len(self.policy.hits[current["label"]]) >= 3:
                    text = PHRASES[current["label"]]
                if event:
                    event.update(schema_version="1.0", event_id=f"{self.run_id}:{frame_id}",
                                 frame_id=frame_id, source_model=self.detector.model_id,
                                 direction_basis="camera_roi", captured_at=captured,
                                 created_at=inferred, measurement_status="not_measured")
                    self.log("warning", **event)
                    self.audio.submit(event)
                self._draw(frame, detections, text, (inferred - started) * 1000)
                ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ok:
                    raise RuntimeError("JPEG encoding failed")
                finished = time.monotonic()
                with self.condition:
                    self.jpeg = encoded.tobytes()
                    self.sequence += 1
                    self.frame_id, self.captured_at = frame_id, captured
                    self.detections, self.current_text = detections, text
                    self.inference_ms = round((inferred - started) * 1000, 1)
                    self.error = ""
                    self.timings.append((finished - captured) * 1000)
                    self.completed.append(finished)
                    if event:
                        self.events.append(event)
                    self.condition.notify_all()
                self.stopped.wait(max(0, self.frame_period - (time.monotonic() - started)))
            except Exception as exc:
                self.policy.reset()
                changed = str(exc) != self.error
                with self.condition:
                    self.jpeg, self.detections, self.error = None, [], str(exc)
                if changed:
                    self.audio.clear_pending()
                    self.log("error", message=str(exc))
                    self.audio.submit({"phrase_id": "visual_unavailable", "ttl_ms": 1500})
                self.stopped.wait(1)

    def _draw(self, frame, detections, text, inference_ms):
        h, w = frame.shape[:2]
        roi = (self.policy.roi * [w, h]).astype(np.int32)
        cv2.polylines(frame, [roi], True, (100, 100, 100), 1)
        for d in detections:
            x1, y1, x2, y2 = (int(v) for v in d["bbox"])
            color = (0, 180, 255) if d.get("ahead") else (60, 230, 80)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f'{d["label"]} {d["confidence"]:.0%}'
            cv2.putText(frame, label, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, color, 2, cv2.LINE_AA)
        cv2.rectangle(frame, (0, h - 30), (w, h), (20, 20, 20), -1)
        message = text or f"YOLO11n | {inference_ms:.0f} ms | No supported target in focus area"
        cv2.putText(frame, message, (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX,
                    0.48, (240, 240, 240), 1, cv2.LINE_AA)

    def status(self):
        with self.condition:
            camera_status = self.camera.status()
            fresh = self.jpeg is not None and time.monotonic() - self.captured_at <= 1.5
            elapsed = self.completed[-1] - self.completed[0] if len(self.completed) > 1 else 0
            ready = fresh and not self.error and camera_status["ready"]
            return {"ready": ready,
                    "error": self.error or camera_status["error"] or ("" if ready else "No fresh result"),
                    "camera": camera_status, "run_id": self.run_id,
                    "frame_id": self.frame_id,
                    "frame_age_ms": round((time.monotonic() - self.captured_at) * 1000) if self.captured_at else None,
                    "fps": round((len(self.completed) - 1) / elapsed, 1) if elapsed else 0,
                    "inference_ms": self.inference_ms,
                    "latency_p95_ms": round(float(np.percentile(self.timings, 95)), 1) if self.timings else None,
                    "detections": self.detections if ready else [],
                    "current_text": self.current_text if ready else "",
                    "recent_events": list(self.events),
                    "model": Detector.model_id, "model_state": self.model_state,
                    "audio": {"state": self.audio.state, "error": self.audio.error,
                              "last_played": self.audio.last_played}}

    def image(self):
        with self.condition:
            if not self.camera.status()["ready"] or time.monotonic() - self.captured_at > 1.5:
                return None
            return self.jpeg


def handler_for(demo):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            try:
                if path == "/":
                    self.reply(PAGE, "text/html; charset=utf-8")
                elif path == "/status":
                    self.reply(json.dumps(demo.status()).encode(), "application/json")
                elif path == '/audio-outputs' and hasattr(demo, 'audio_outputs'):
                    try:
                        self.reply(json.dumps(demo.audio_outputs.status()).encode(), 'application/json')
                    except RuntimeError as exc:
                        self.reply(json.dumps({'error': str(exc)}).encode(), 'application/json', 503)
                elif path in ("/frame.jpg", "/snapshot.jpg"):
                    image = demo.image()
                    self.reply(image or b"No fresh frame", "image/jpeg" if image else "text/plain",
                               200 if image else 503)
                elif path == "/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    previous = 0
                    while not demo.stopped.is_set():
                        with demo.condition:
                            demo.condition.wait_for(lambda: demo.sequence != previous or demo.stopped.is_set(), 1)
                            previous = demo.sequence
                            image = demo.image()
                        if image:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                                             str(len(image)).encode() + b"\r\n\r\n" + image + b"\r\n")
                            self.wfile.flush()
                else:
                    self.reply(b"Not found", "text/plain", 404)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            if self.path == '/audio-output' and hasattr(demo, 'audio_outputs'):
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    self.reply(b'{"error":"JSON required"}', 'application/json', 415)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 4096:
                        raise ValueError('Invalid audio request size')
                    request = json.loads(self.rfile.read(length))
                    result = demo.audio_outputs.select(request)
                    self.reply(json.dumps(result).encode(), 'application/json')
                except (ValueError, UnicodeError) as exc:
                    self.reply(json.dumps({'error': str(exc)}).encode(), 'application/json', 400)
                except (RuntimeError, OSError) as exc:
                    self.reply(json.dumps({'error': str(exc)}).encode(), 'application/json', 503)
            elif self.path == "/capture":
                image = demo.image()
                if image is None:
                    self.reply(b'{"error":"No fresh frame"}', "application/json", 503)
                    return
                directory = ROOT / "captures"
                directory.mkdir(exist_ok=True)
                path = directory / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] + ".jpg")
                path.write_bytes(image)
                self.reply(json.dumps({"path": str(path), "bytes": len(image)}).encode(), "application/json")
            elif self.path == "/test-audio":
                phrase = 'crowd_ahead' if getattr(demo,'awareness_mode',False) else 'chair_ahead'
                demo.audio.submit({"phrase_id": phrase, "ttl_ms": 1500})
                self.reply(json.dumps({"state": demo.audio.state}).encode(), "application/json")
            else:
                self.reply(b"Not found", "text/plain", 404)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/camera.example.toml")
    args = parser.parse_args()
    with Path(args.config).open("rb") as f:
        config = tomllib.load(f)
    demo = Demo(config)
    server = ThreadingHTTPServer((config["server"]["host"], config["server"]["port"]), handler_for(demo))
    demo.start()
    def stop_on_signal(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_on_signal)
    print(f'Live camera: http://{config["server"]["host"]}:{config["server"]["port"]}/', flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        demo.close()


if __name__ == "__main__":
    main()
