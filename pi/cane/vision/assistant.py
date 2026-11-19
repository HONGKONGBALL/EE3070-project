"""A single push-to-talk request with hardware-independent press/release input."""

import threading
import time
import uuid

from .assistant_media import AlsaRecorder, EspeakSynthesizer, MockRecognizer, MockRecorder, OfflineRecognizer, shared_jpeg
from .assistant_provider import AssistantError, MockVisionProvider, OpenAICompatibleVisionProvider


class VoiceAssistant:
    def __init__(self, config, camera, audio, *, synthesizer=None):
        self.mode = config.get("mode", "mock")
        self.question_language = config.get("question_language", "en")
        self.answer_language = config.get("answer_language", "en")
        if self.mode not in {"mock", "psyche"} or any(
                lang not in {"en", "zh"} for lang in (self.question_language, self.answer_language)):
            raise ValueError("Invalid assistant mode or language")
        self.max_record = float(config.get("max_record_s", 8))
        self.max_age = float(config.get("max_scene_age_s", 12))
        self.network_timeout = float(config.get("request_timeout_s", 8))
        self.asr_timeout = float(config.get("asr_timeout_s", 6))
        if not (0.25 <= self.max_record <= 20 and 1 <= self.max_age <= 30 and
                0.1 <= self.network_timeout <= 20 and 0.1 <= self.asr_timeout <= 15):
            raise ValueError("Invalid assistant timing limits")
        if self.mode == "mock":
            self.recorder = MockRecorder()
            self.recognizer = MockRecognizer(self.question_language)
            self.provider = MockVisionProvider()
        else:
            self.recorder = AlsaRecorder(config.get("microphone", "default"), self.max_record)
            self.recognizer = OfflineRecognizer(config.get("vosk_model_dir", "models"), self.question_language, self.asr_timeout)
            self.provider = OpenAICompatibleVisionProvider(self.network_timeout)
        self.synthesizer = synthesizer or EspeakSynthesizer()
        self.camera = camera
        self.audio = audio
        self.lock = threading.Lock()
        self.closed = False
        self.state = "IDLE"
        self.request_id = None
        self.error = ""
        self.answer = ""
        self.frame_id = None
        self.captured_at = None
        self.started_at = None
        self.updated_at = time.time()
        self.recording = self.timer = self.worker = None

    def _state(self, state, error=""):
        self.state, self.error = state, error
        self.updated_at = time.time()

    def status(self):
        with self.lock:
            answer = self.answer if self.captured_at and time.monotonic() - self.captured_at <= self.max_age else ""
            return {"state":self.state, "error":self.error, "mode":self.mode, "request_id":self.request_id,
                    "frame_id":self.frame_id, "answer":answer, "answer_language":self.answer_language,
                    "question_language":self.question_language, "updated_at":self.updated_at}

    def press(self):
        with self.lock:
            if self.closed or self.state not in {"IDLE", "ERROR"} or (self.worker and self.worker.is_alive()):
                raise AssistantError("busy")
            self.request_id = "assistant:" + uuid.uuid4().hex
            self.answer = ""
            self.frame_id = self.captured_at = None
            try:
                self.provider.check()
                if not self.audio.sink.enabled:
                    raise AssistantError("audio_disabled")
                self.recording = self.recorder.start()
            except AssistantError as exc:
                self._state("ERROR", str(exc))
                raise
            self.started_at = time.monotonic()
            self._state("RECORDING")
            self.timer = threading.Timer(self.max_record, self.release, args=(self.request_id,), kwargs={"timed_out":True})
            self.timer.daemon = True
            self.timer.start()
            return self.request_id

    def release(self, request_id, *, timed_out=False, cancelled=False):
        with self.lock:
            if self.state != "RECORDING" or request_id != self.request_id:
                return False
            self.timer.cancel()
            recording, self.recording = self.recording, None
            too_short = time.monotonic() - self.started_at < 0.18
            error = "recording_limit" if timed_out else "" if cancelled else "empty_audio" if too_short else None
            self._state("TRANSCRIBING")
            self.worker = threading.Thread(target=self._process, args=(request_id, recording, error),
                                           daemon=True, name="cane-assistant")
            self.worker.start()
            return True

    def _process(self, request_id, recording, recording_error):
        try:
            pcm = recording.stop()
            if recording_error is not None:
                if recording_error:
                    raise AssistantError(recording_error)
                with self.lock:
                    self._state("IDLE")
                return
            if len(pcm) < 3200:
                raise AssistantError("empty_audio")
            jpeg, frame_id, captured_at = shared_jpeg(self.camera)
            question = self.recognizer.transcribe(pcm)[:500].strip()
            if not question:
                raise AssistantError("empty_audio")
            with self.lock:
                if self.closed:
                    return
                self.frame_id, self.captured_at = frame_id, captured_at
                self._state("ANALYZING")
            if time.monotonic() - captured_at > self.max_age:
                raise AssistantError("scene_expired")
            answer = self.provider.describe(question, jpeg, self.answer_language)
            if time.monotonic() - captured_at > self.max_age:
                raise AssistantError("scene_expired")
            response_pcm = self.synthesizer.synthesize(answer, self.answer_language)
            with self.lock:
                if self.closed:
                    return
                if time.monotonic() - captured_at > self.max_age:
                    raise AssistantError("scene_expired")
                self.answer = answer
            # Never acquire the audio lock while holding the assistant lock.
            if not self.audio.submit_assistant(request_id, response_pcm, captured_at, self.max_age):
                raise AssistantError("audio_busy")
        except AssistantError as exc:
            with self.lock:
                self._state("ERROR", str(exc))
        except Exception:
            with self.lock:
                self._state("ERROR", "assistant_error")

    def valid(self, request_id, now):
        with self.lock:
            # Expiration gates playback start. A spoken answer may finish unless interrupted.
            return not self.closed and request_id == self.request_id and (
                self.state == "SPEAKING" or (self.state == "ANALYZING" and self.captured_at is not None
                                             and now - self.captured_at <= self.max_age))

    def delivery(self, event_id, action, now):
        with self.lock:
            if self.closed or event_id != self.request_id or self.state not in {"ANALYZING", "SPEAKING"}:
                return
            if action == "started":
                self._state("SPEAKING")
            elif action == "completed":
                self._state("IDLE")
            elif action in {"interrupted", "replaced", "invalid"}:
                self.answer = ""
                self._state("IDLE", "interrupted")
            elif action in {"expired", "failed", "disabled", "rejected"}:
                error = {"expired":"scene_expired", "failed":"playback_failed", "disabled":"audio_disabled",
                         "rejected":"audio_busy"}[action]
                self._state("ERROR", error)

    def close(self):
        with self.lock:
            self.closed = True
            if self.timer:
                self.timer.cancel()
            recording, self.recording = self.recording, None
            self.answer = ""
        if recording:
            recording.stop()
