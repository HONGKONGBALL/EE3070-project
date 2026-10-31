"""One interruptible PipeWire sink behind the existing output arbiter."""

from pathlib import Path
import subprocess
import threading
import time
import uuid
from cane.arbiter import OutputArbiter
from cane.model import OutputRequest, Priority


class PipeWireAudio:
    def __init__(self, directory, enabled, target, record):
        self.directory, self.enabled, self.target, self.record = Path(directory), enabled, target, record
        self.state, self.error, self.last_played = ("ready" if enabled else "disabled"), "", None
        self.proc, self.thread = None, None
        self.cancelled = None
        self.playback_id = None

    def play(self, clip_ids, text=None):
        self.stop()
        playback_id=self.playback_id=uuid.uuid4().hex[:16]
        def record(kind,**fields):self.record(kind,playback_id=playback_id,**fields)
        if not self.enabled:
            record("audio_disabled", phrase_id=clip_ids[0], text=text)
            return
        phrase = clip_ids[0]
        command = ["pw-play"] + (["--target", self.target] if self.target else []) + [str(self.directory/(phrase+".wav"))]
        started = time.monotonic()
        try:
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as exc:
            self.state, self.error = "error", str(exc)
            record("audio_error", phrase_id=phrase, message=str(exc))
            return
        self.proc, self.state = proc, "playing"
        cancelled = self.cancelled = threading.Event()
        record("audio_start", phrase_id=phrase, process_started_at=started)

        def finished():
            try:
                _, error = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                self.state,self.error = "error","Playback timed out"
                record("audio_error",phrase_id=phrase,message=self.error)
                return
            if cancelled.is_set():
                record("audio_interrupted", phrase_id=phrase)
                self.state = "ready"
            elif proc.returncode == 0:
                self.last_played = {"phrase_id": phrase, "playback_id":playback_id,"process_started_at": started,
                                    "finished_at": time.monotonic()}
                self.record("audio_done", **self.last_played)
                self.state, self.error = "ready", ""
            elif proc.returncode < 0:
                record("audio_interrupted", phrase_id=phrase)
                self.state = "ready"
            else:
                self.state, self.error = "error", error.decode(errors="replace")[:300]
                record("audio_error", phrase_id=phrase, message=self.error)

        self.thread = threading.Thread(target=finished, daemon=True, name="pipewire")
        self.thread.start()

    def stop(self):
        proc, thread = self.proc, self.thread
        requested=None
        if proc and proc.poll() is None:
            requested=time.monotonic()
            self.record('audio_stop_requested',playback_id=self.playback_id,stop_requested_at=requested)
            self.cancelled.set()
            proc.terminate()
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                proc.kill()
        if thread:
            thread.join(timeout=0.3)
        if requested is not None:
            self.record('audio_stopped' if proc.poll() is not None else 'audio_stop_pending',playback_id=self.playback_id,stopped_at=time.monotonic(),
                        stop_latency_ms=round((time.monotonic()-requested)*1000,1))
        self.proc, self.thread = None, None

    def busy(self):
        return self.thread is not None and self.thread.is_alive()


class Speech:
    def __init__(self, directory, enabled, target="", record=None):
        self.record = record or (lambda *_a, **_k: None)
        self.sink = PipeWireAudio(directory, enabled, target, self.record)
        self.validator = lambda req: True
        self.arbiter = OutputArbiter(self.sink, repeat_cooldown_s=3, max_pending=2,
                                     ordinary_interval_s=3,
                                     valid_request=lambda req: self.validator(req),
                                     record_event=self._record_arbiter)

    def _record_arbiter(self,event):
        self.record('arbiter',action=event.action,phrase_id=event.prompt_id,priority=event.priority,
                    event_id=event.event_id,monotonic=event.t,recorded_at=time.monotonic(),
                    playback_id=self.sink.playback_id if event.action in ('played','preempted') else None)

    @property
    def state(self): return self.sink.state
    @property
    def error(self): return self.sink.error
    @property
    def last_played(self): return self.sink.last_played

    def submit(self, event):
        submitted=time.monotonic()
        req = OutputRequest(event["phrase_id"], Priority[event.get("priority", "SYSTEM")],
                            text=event.get("text"), created=event.get("created_at", time.monotonic()),
                            ttl_s=event.get("ttl_ms",1500)/1000, event_id=event.get("event_id", ""))
        accepted = self.arbiter.submit(req)
        self.record("audio_queued" if accepted else "audio_rejected", phrase_id=req.prompt_id, event_id=req.event_id,
                    submitted_at=submitted,accepted_at=time.monotonic())
        return accepted

    def tick(self):
        self.arbiter.tick()

    def clear_pending(self):
        self.arbiter.cancel_pending(lambda req: bool(req.event_id))

    def close(self):
        self.sink.stop()
