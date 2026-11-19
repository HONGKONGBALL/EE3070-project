"""One interruptible PipeWire sink behind the existing output arbiter."""

from pathlib import Path
from collections import deque, OrderedDict
import subprocess
import threading
import time
import uuid
import wave
from cane.arbiter import OutputArbiter
from cane.model import OutputRequest, Priority
from .voice import VOICE


class PipeWireAudio:
    def __init__(self, directory, enabled, target, record):
        self.directory, self.enabled, self.target, self.record = Path(directory), enabled, target, record
        self.catalog = None
        self.state, self.error, self.last_played = ("ready" if enabled else "disabled"), "", None
        self.proc, self.thread = None, None
        self.cancelled = None
        self.playback_id = None

    def play(self, clip_ids, text=None, *, context=None, pcm=None):
        self.stop()
        playback_id=self.playback_id=uuid.uuid4().hex[:16]
        def record(kind,**fields):self.record(kind,**(context or {}),playback_id=playback_id,**fields)
        if not self.enabled:
            record("audio_disabled", phrase_id=clip_ids[0], text=text)
            return
        phrase = clip_ids[0]
        command = ["pw-play"] + (["--target", self.target] if self.target else [])
        try:
            if pcm is None and len(clip_ids) > 1:
                pcm = self._join_clips(clip_ids)
            if pcm is None:
                command.append(str(self._clip_path(phrase)))
        except (OSError, EOFError, ValueError, wave.Error) as exc:
            self.state, self.error = "error", str(exc)
            record("audio_error", phrase_id=phrase, message=str(exc))
            return
        if pcm is not None:
            rate, samples = pcm
            command.extend(["--raw", "--rate", str(rate), "--channels", "1", "--format", "s16", "-"])
        launch_requested = time.monotonic()
        try:
            pipes = {"stdin": subprocess.PIPE} if pcm is not None else {}
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **pipes)
        except OSError as exc:
            self.state, self.error = "error", str(exc)
            record("audio_error", phrase_id=phrase, message=str(exc))
            return
        started = time.monotonic()
        self.proc, self.state = proc, "playing"
        cancelled = self.cancelled = threading.Event()
        record("audio_start", phrase_id=phrase, launch_requested_at=launch_requested, process_started_at=started,
               timing_basis="pw-play process launched; physical sound onset not measured")

        def finished():
            try:
                _, error = (proc.communicate(timeout=5) if pcm is None else
                            proc.communicate(input=samples, timeout=20))
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
                record("audio_done", phrase_id=phrase, process_started_at=started, finished_at=self.last_played["finished_at"])
                self.state, self.error = "ready", ""
            elif proc.returncode < 0:
                record("audio_interrupted", phrase_id=phrase)
                self.state = "ready"
            else:
                self.state, self.error = "error", error.decode(errors="replace")[:300]
                record("audio_error", phrase_id=phrase, message=self.error)

        self.thread = threading.Thread(target=finished, daemon=True, name="pipewire")
        self.thread.start()

    def _clip_path(self, phrase):
        path = self.directory / (phrase + ".wav")
        if self.catalog is not None and not path.is_file():
            return self.catalog / path.name
        return path

    def _join_clips(self, clip_ids):
        samples = bytearray()
        rate = None
        for phrase in clip_ids:
            with wave.open(str(self._clip_path(phrase)), "rb") as clip:
                if rate is None:
                    rate = clip.getframerate()
                if (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) != (1, 2, rate):
                    raise ValueError("Navigation clips must have matching mono PCM16 formats")
                samples.extend(clip.readframes(clip.getnframes()))
        return rate, bytes(samples)

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
    def __init__(self, directory, enabled, target="", record=None, *, ordinary_interval_s=1.5,
                 allowed_phrases=None, haptic=None, info_preempts_navigation=False):
        self.record = record or (lambda *_a, **_k: None)
        self.allowed_phrases = None if allowed_phrases is None else frozenset(allowed_phrases)
        self.on_delivery = lambda *_a: None
        self.contexts = OrderedDict()
        self.deliveries = deque(maxlen=16)
        self.control_lock = threading.RLock()
        self.sink = PipeWireAudio(directory, enabled, target, self._record_audio)
        self.validator = lambda req: True
        self.assistant_clip = None
        self.arbiter = OutputArbiter(self, haptic, repeat_cooldown_s=3, max_pending=2,
                                     ordinary_interval_s=ordinary_interval_s,
                                     info_preempts_navigation=info_preempts_navigation,
                                     lock=self.control_lock,
                                     valid_request=lambda req: self.validator(req),
                                     record_event=self._record_arbiter)

    def play(self, clips, text=None):
        req = self.arbiter.current
        context = self.contexts.get(req.event_id, {}) if req else {}
        if req and req.priority.is_assistant:
            _, pcm = self.assistant_clip
            self.assistant_clip = None
            self.sink.play(clips, context=context, pcm=pcm)
        else:
            self.sink.play(clips, text, context=context)

    def stop(self) -> None:
        with self.control_lock:
            self.sink.stop()
    def busy(self): return self.sink.busy()

    def _delivery(self, event_id, action, now):
        if not event_id: return
        if action in {"expired", "invalid", "rejected", "replaced"}:
            if self.assistant_clip and self.assistant_clip[0] == event_id:
                self.assistant_clip = None
        self.deliveries.append({"event_id":event_id,"action":action,"at":now})
        self.on_delivery(event_id,action,now)

    def _record_audio(self, kind, **fields):
        now = time.monotonic()
        event_id = fields.get("event_id", "")
        event = self.contexts.get(event_id, {})
        if kind == "audio_start":
            fields["queue_to_process_start_ms"] = round((now-event.get("submitted_at",now))*1000,1)
            fields["capture_to_process_start_ms"] = round((now-event.get("captured_at",now))*1000,1)
            fields["candidate_to_process_start_ms"] = round((now-event.get("first_positive_candidate_at",now))*1000,1)
        self.record(kind, **fields)
        action = {"audio_start":"started","audio_done":"completed","audio_disabled":"disabled",
                  "audio_error":"failed","audio_interrupted":"interrupted"}.get(kind)
        if action: self._delivery(event_id,action,now)

    def delivery_status(self):
        return {"recent":list(self.deliveries),"queue_capacity":2,
                "timing_basis":"Process start is not measured physical sound onset"}

    def _record_arbiter(self,event):
        self.record('arbiter',action=event.action,phrase_id=event.prompt_id,priority=event.priority,
                    event_id=event.event_id,monotonic=event.t,recorded_at=time.monotonic(),
                    playback_id=self.sink.playback_id if event.action in ('played','preempted') else None)

        action={"expired":"expired","dropped_invalid":"invalid","dropped_full":"rejected",
                "replaced":"replaced","cancelled":"invalid","preempted":"interrupted"}.get(event.action)
        if action:self._delivery(event.event_id,action,event.t)

    @property
    def state(self): return self.sink.state
    @property
    def error(self): return self.sink.error
    @property
    def last_played(self): return self.sink.last_played

    def submit(self, event):
        with self.control_lock:
            return self._submit(event)

    def submit_assistant(self, event_id, pcm, captured_at, max_age, *, navigation=False):
        with self.control_lock:
            self.assistant_clip = (event_id, pcm)
            priority = "ASSISTANT_NAV" if navigation else "ASSISTANT"
            accepted = self._submit({"phrase_id": "assistant_reply", "priority": priority,
                                     "event_id": event_id, "created_at": captured_at,
                                     "ttl_ms": max_age * 1000}, assistant=True)
            if not accepted:
                self.assistant_clip = None
            return accepted

    def cancel_assistant(self, event_id):
        with self.control_lock:
            self.arbiter.cancel_pending(lambda req: req.event_id == event_id)
            current = self.arbiter.current
            if current and current.event_id == event_id:
                self.sink.stop()

    def _submit(self, event, *, assistant=False):
        if not assistant and self.allowed_phrases is not None and event['phrase_id'] not in self.allowed_phrases:
            self.record('audio_filtered', phrase_id=event['phrase_id'], event_id=event.get('event_id',''),
                        reason='outside_v1_awareness_voice_scope')
            return False
        submitted=time.monotonic()
        priority = event.get('priority','SYSTEM')
        if self.allowed_phrases is not None and event['phrase_id']==VOICE['CROWD'][0]:
            priority = 'INFO'
        req = OutputRequest(event["phrase_id"], Priority[priority],
                            text=event.get("text"), created=event.get("created_at", time.monotonic()),
                            ttl_s=event.get("ttl_ms",1500)/1000, event_id=event.get("event_id", ""))
        self.contexts[req.event_id] = {key:event[key] for key in ("event_id","episode_id","warning_family",
            "captured_at","first_positive_candidate_at","scene_verification_completed_at","confirmed_at") if key in event}
        self.contexts[req.event_id]["submitted_at"] = submitted
        while len(self.contexts)>64:self.contexts.popitem(last=False)
        accepted = self.arbiter.submit(req)
        self._delivery(req.event_id,"queued" if accepted else "rejected",time.monotonic())
        self.record("audio_queued" if accepted else "audio_rejected", phrase_id=req.prompt_id, event_id=req.event_id,
                    submitted_at=submitted,accepted_at=time.monotonic())
        return accepted

    def tick(self):
        with self.control_lock:
            self.arbiter.tick()

    def configure_output(self, enabled, target):
        with self.control_lock:
            self.clear_pending()
            self.sink.stop()
            self.sink.enabled, self.sink.target = enabled, target
            self.sink.state, self.sink.error = ('ready' if enabled else 'disabled'), ''
            self.record('audio_output_changed', enabled=enabled, target=target)

    def clear_pending(self):
        self.arbiter.cancel_pending(lambda req: bool(req.event_id))

    def close(self) -> None:
        self.stop()
