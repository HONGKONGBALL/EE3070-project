"""The local spoken assistant runs independently of the vision worker."""

from collections import deque
from contextlib import contextmanager
from functools import cached_property
import io
import logging
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .assistant_media import shared_jpeg
from .assistant_provider import AssistantError
from .voice_commands import MockNavigationAdapter, confirmed, route_command
from .wake_word import DISPLAY_PHRASE, WakeDetector


LOG = logging.getLogger(__name__)


class WakeAssistant:
    def __init__(self, config, camera, audio, *, microphone=None, speech=None, provider=None,
                 detector=None, navigation=None):
        self.camera, self.audio = camera, audio
        self.microphone = microphone if microphone is not None else self._microphone(config.get("microphone", "default"))
        self.models = Path(config.get("speech_models_dir", "voice/models"))
        self._speech, self._provider = speech, provider
        self.ollama_port = config.get("ollama_port", 11434)
        self.request_timeout = config.get("request_timeout_s", 20)
        self.navigation = navigation if navigation is not None else MockNavigationAdapter()
        self.wake_enabled = bool(config.get("wake_enabled", False))
        self.dashboard_test = bool(config.get("dashboard_wake_test", False))
        self.ptt_gpio = config.get("ptt_gpio")
        if self.ptt_gpio is not None and (type(self.ptt_gpio) is not int or not 0 <= self.ptt_gpio <= 27):
            raise ValueError("ptt_gpio must be a Raspberry Pi BCM GPIO number")
        self.gpio_button = None
        self.command_timeout = float(config.get("max_record_s", 8))
        self.max_age = float(config.get("max_scene_age_s", 12))
        self.silence_level = float(config.get("silence_rms", 220))
        self._detector = detector
        self.confidence = float(config.get("wake_confidence", 0.70))
        if not (1 <= self.command_timeout <= 20 and 1 <= self.max_age <= 30
                and 0 <= self.silence_level < 32768 and 0 <= self.confidence <= 1):
            raise ValueError("Invalid voice timeout, silence threshold, or wake confidence")

        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.trigger_ready = threading.Event()
        self.worker = threading.Thread(target=self._run, daemon=True, name="cane-wake-assistant")
        self.state, self.error = "PASSIVE_LISTENING", ""
        self.recent = deque([self.state], maxlen=16)
        self.trigger = self.request_id = self.clip = None
        self.released = self.cancelled = False
        self.playback_done = threading.Event()
        self.playback_action = ""
        self.answer = ""
        self.frame_id = None
        self.captured_at = None

    @cached_property
    def detector(self):
        return self._detector if self._detector is not None else WakeDetector(
            self.models / "vosk-model-small-ko-0.22", lambda: self.speech.recognizer_model, self.confidence)

    @cached_property
    def speech(self):
        if self._speech is not None:
            return self._speech
        from voice_demo import LocalSpeech

        return LocalSpeech(self.models)

    @cached_property
    def provider(self):
        if self._provider is not None:
            return self._provider
        from ollama_vision import InferenceSettings, OllamaVisionProvider

        return OllamaVisionProvider(
            self.ollama_port, timeout=self.request_timeout, threads=3, settings=InferenceSettings(prompt="grounded"))

    @staticmethod
    def _microphone(choice):
        @contextmanager
        def open_stream():
            from voice_audio import local_audio, select_endpoint

            try:
                backend = local_audio()
                endpoints = backend.endpoints("input")
                device = select_endpoint(endpoints, None if choice == "default" else choice, "input")
                with backend.stream(device) as read:
                    yield read
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                raise AssistantError("microphone_unavailable") from exc

        return open_stream

    def _state(self, state, error=""):
        with self.lock:
            self.state, self.error = state, error
            if self.recent[-1] != state:
                self.recent.append(state)

    def status(self):
        with self.lock:
            fresh = self.captured_at is None or time.monotonic() - self.captured_at <= self.max_age
            return {"state": self.state, "error": self.error, "mode": "ollama", "request_id": self.request_id,
                    "wake_enabled": self.wake_enabled, "wake_phrase": DISPLAY_PHRASE,
                    "dashboard_wake_test": self.dashboard_test, "recent_states": list(self.recent),
                    "ptt_gpio_unavailable": self.ptt_gpio is not None and self.gpio_button is None,
                    "frame_id": self.frame_id, "answer": self.answer if fresh else "",
                    "answer_language": "en", "question_language": "en"}

    def start(self):
        self.worker.start()
        if self.ptt_gpio is not None:
            from .ptt_button import GpioPushToTalk

            try:
                self.gpio_button = GpioPushToTalk(self.ptt_gpio, self)
            except Exception:
                LOG.exception("Physical push-to-talk button unavailable on GPIO %s", self.ptt_gpio)

    def close(self):
        self.stop.set()
        self.trigger_ready.set()
        if self.gpio_button is not None:
            self.gpio_button.close()
        self.playback_done.set()
        with self.lock:
            clip = self.clip
        if clip:
            self.audio.cancel_assistant(clip)
        if self.worker.is_alive():
            self.worker.join(timeout=1)

    def _activate(self, trigger):
        with self.lock:
            if self.stop.is_set() or self.state != "PASSIVE_LISTENING" or self.trigger:
                raise AssistantError("busy")
            self.request_id = uuid.uuid4().hex
            self.trigger = trigger
            self.released = self.cancelled = False
            self.trigger_ready.set()
            return self.request_id

    def press(self):
        return self._activate("ptt")

    def test_wake(self):
        if not self.dashboard_test:
            raise AssistantError("test_disabled")
        return self._activate("wake")

    def release(self, request_id, *, cancelled=False):
        with self.lock:
            if not request_id or request_id != self.request_id or self.released:
                return False
            if self.trigger != "ptt":
                return False
            self.released, self.cancelled = True, cancelled
            return True

    def valid(self, event_id, now):
        with self.lock:
            return not self.stop.is_set() and event_id == self.clip

    def delivery(self, event_id, action, now):
        if action in {"completed", "interrupted", "expired", "invalid", "replaced", "failed", "disabled", "rejected"}:
            with self.lock:
                if event_id == self.clip:
                    self.playback_action = action
                    self.playback_done.set()

    def _speak(self, text, *, navigation=False, captured_at=None, next_state="SPEAKING"):
        from voice_audio import read_wave

        samples, rate, channels = read_wave(self.speech.synthesize(text))
        if channels != 1 or not samples:
            raise AssistantError("tts_unavailable")
        if self.stop.is_set():
            return False
        now = time.monotonic()
        if captured_at is not None and now - captured_at >= self.max_age:
            raise AssistantError("scene_expired")
        event_id = "assistant:" + uuid.uuid4().hex
        with self.lock:
            if self.stop.is_set():
                return False
            self.clip = event_id
            self.playback_action = ""
            self.playback_done.clear()
            self._state(next_state)
        try:
            accepted = self.audio.submit_assistant(event_id, (rate, samples), captured_at or now,
                                                   self.max_age, navigation=navigation)
            if not accepted:
                return False
            if not self.playback_done.wait(20):
                self.audio.cancel_assistant(event_id)
            return self.playback_action == "completed"
        finally:
            with self.lock:
                self.clip = None

    def _capture(self, mic, *, ptt=False):
        import numpy as np

        end = time.monotonic() + self.command_timeout
        pcm = bytearray()
        heard = False
        silent = 0
        while not self.stop.is_set() and time.monotonic() < end and not (ptt and self.released):
            block = mic(0.2)
            if not block:
                continue
            if self.audio.busy():
                # A warning can enter the microphone. Discard the incomplete utterance.
                pcm.clear()
                heard, silent = False, 0
                continue
            pcm.extend(block)
            if ptt:
                continue
            values = np.frombuffer(block, dtype="<i2").astype(np.float32)
            if np.sqrt(np.mean(values * values)) >= self.silence_level:
                heard, silent = True, 0
            elif heard:
                silent += len(block)
                if silent >= 22_400:
                    break
        if self.stop.is_set():
            return b""
        if ptt:
            if not self.released:
                raise AssistantError("recording_limit")
            return bytes(pcm) if not self.cancelled else b""
        return bytes(pcm) if heard else b""

    def _activation(self):
        while not self.stop.is_set():
            if not self.wake_enabled and not self.trigger:
                self.trigger_ready.wait()
                continue
            if self.trigger == "wake":
                return "wake", None
            with self.microphone() as mic:
                muted_until = 0
                if self.wake_enabled:
                    self.detector.reset()
                while not self.stop.is_set():
                    if self.trigger == "ptt":
                        self._state("LISTENING_FOR_COMMAND")
                        return "ptt", self._capture(mic, ptt=True)
                    if self.trigger == "wake":
                        return "wake", None
                    block = mic(0.2)
                    if self.audio.busy():
                        self.detector.reset()
                        muted_until = time.monotonic() + 0.25
                    elif block and time.monotonic() >= muted_until and self.detector.feed(block):
                        with self.lock:
                            if self.trigger is None and not self.stop.is_set():
                                self.trigger = "wake"
                                return "wake", None
        return None, None

    def _transcribe(self, pcm):
        self._state("TRANSCRIBING")
        return self.speech.transcribe(pcm).strip() if pcm else ""

    def _turn(self, kind, pcm):
        if not self.audio.sink.enabled:
            raise AssistantError("audio_disabled")
        if kind == "wake":
            self._state("WAKE_DETECTED")
            if not self._speak("Yes?", next_state="WAKE_DETECTED"):
                return
            self._state("LISTENING_FOR_COMMAND")
            with self.microphone() as mic:
                pcm = self._capture(mic)
        question = self._transcribe(pcm)
        if not question or self.stop.is_set():
            return
        self._state("ROUTING_COMMAND")
        action, destination = route_command(question)
        if action == "vision":
            self._state("PROCESSING")
            jpeg, self.frame_id, self.captured_at = shared_jpeg(self.camera)
            from PIL import Image

            question = question[0].upper() + question[1:]
            if question[-1] not in ".?!":
                first_word = question.split()[0].split("'")[0].casefold()
                question += "?" if first_word in {"what", "where", "who", "how", "which", "is", "are", "can", "could",
                                                 "should", "do", "does"} else "."
            with Image.open(io.BytesIO(jpeg)) as frame:
                result = self.provider.answer_question(question, frame)
            if not result.success:
                raise AssistantError("provider_error")
            answer = result.answer if any(char.isalpha() for char in result.answer) else (
                "I couldn't make out an answer from this image.")
        else:
            answer = self._navigate(action, destination)
        if answer and self._speak(answer, navigation=action != "vision", captured_at=self.captured_at):
            with self.lock:
                self.answer = answer

    def _navigate(self, action, destination):
        if action == "unsupported_navigation":
            return "Try saying navigate to, followed by the destination."
        if action == "plan":
            if not self._speak(f"Do you want to navigate to {destination}?", navigation=True,
                               next_state="WAITING_FOR_CONFIRMATION"):
                return ""
            with self.microphone() as mic:
                text = self._capture(mic)
            if self.stop.is_set() or not confirmed(self._transcribe(text)):
                return "Navigation cancelled."
            self._state("PROCESSING")
            reply = self.navigation.plan_route(destination)
            return reply.message
        self._state("PROCESSING")
        operation = {"stop": self.navigation.stop_navigation, "next": self.navigation.get_next_instruction,
                     "status": self.navigation.get_navigation_status}[action]
        return operation().message

    def _run(self):
        while not self.stop.is_set():
            try:
                kind, pcm = self._activation()
                if kind is not None:
                    with self.lock:
                        self.answer, self.frame_id, self.captured_at = "", None, None
                    self._turn(kind, pcm)
                self._state("COOLDOWN")
                self.stop.wait(0.55)
            except AssistantError as exc:
                self._state("ERROR", str(exc))
                self.stop.wait(0.6)
            except ImportError:
                self._state("ERROR", "asr_unavailable")
                self.stop.wait(1.5)
            except Exception:
                LOG.exception("Local voice assistant failed")
                self._state("ERROR", "assistant_error")
                self.stop.wait(1.5)
            finally:
                with self.lock:
                    self.trigger = self.request_id = None
                    self.trigger_ready.clear()
                    if not self.stop.is_set():
                        # Preserve the last error until a successfully opened turn starts.
                        self._state("PASSIVE_LISTENING", self.error)
