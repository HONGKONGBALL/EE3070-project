"""The local spoken assistant runs independently of the vision worker."""

from collections import deque
from contextlib import contextmanager, nullcontext
from functools import cached_property
from math import pi, sin
import io
import logging
from pathlib import Path
import re
import struct
import subprocess
import threading
import time
from typing import Protocol
import uuid

from .assistant_media import foreground_is_dark, shared_jpeg, speech_only_pcm
from .assistant_provider import AssistantError
from .voice_commands import MockNavigationAdapter, confirmed, route_command
from .wake_word import DISPLAY_PHRASE, WakeDetector


LOG = logging.getLogger(__name__)
VISION_RETRY = "I couldn't make out the scene."
TOGGLE_CHANNELS = {"vision_button", "navigation_button"}
# A hazard warning can hold the speaker when K4 is tapped. Retry the cue until it is heard.
ACK_WAIT_S = 3.0
ACK_RETRY_S = 0.15
SERVICE_UNAVAILABLE = "The vision service is unavailable."
# Spoken locally by Piper when a cloud turn fails, so the user is not left in silence.
CLOUD_FAILURE_SPEECH = {
    "provider_timeout": "The vision service timed out. Please try again.",
    "provider_busy": "The vision service is busy. Please try again.",
    "budget_exhausted": "The daily vision limit has been reached.",
    "scene_expired": "That took too long. Please ask again.",
    "recording_limit": "Recording stopped. Please tap and ask again.",
    "microphone_unavailable": "The microphone is unavailable.",
}
# The same messages for a Mandarin assistant (answer_language = "zh").
ZH_FAILURE_SPEECH = {
    "provider_timeout": "云端服务超时，请再试一次。",
    "provider_busy": "云端服务繁忙，请再试一次。",
    "budget_exhausted": "今天的云端请求次数已用完。",
    "scene_expired": "等待时间太长，请重新提问。",
    "recording_limit": "录音已停止，请重新按键再说。",
    "microphone_unavailable": "麦克风不可用。",
}
TEXT = {
    "en": {"service_unavailable": SERVICE_UNAVAILABLE, "vision_retry": VISION_RETRY,
           "not_caught": "I didn't catch that. Please tap and ask again.",
           "visual_safety": "I can't tell from one view whether it's safe to proceed.",
           "use_navigation_button": "Use the navigation button for route commands.",
           "camera_unavailable": "The camera view is unavailable."},
    "zh": {"service_unavailable": "云端服务不可用。", "vision_retry": "看不清画面。",
           "not_caught": "没有听清，请重新按键再说。",
           "visual_safety": "只凭一个画面，我无法判断能不能安全通过。",
           "use_navigation_button": "导航请用K2按键。",
           "camera_unavailable": "摄像头画面不可用。"},
}
def _tone(frequency, samples, level=12000):
    return b"".join(struct.pack("<h", int(level * sin(2 * pi * frequency * n / 16000) *
                                          min(1, n / 160, (samples - n) / 160))) for n in range(samples))


# 0.35 s of silence lets a Bluetooth sink wake up. The rising two-tone cue is loud enough for earbuds.
ACK = bytes(2 * 5600) + _tone(880, 2880) + _tone(1320, 3520)


class ASRProvider(Protocol):
    def transcribe(self, pcm: bytes) -> str: ...


class TTSProvider(Protocol):
    def synthesize(self, text: str) -> bytes: ...


def short_visual_answer(text, retry=VISION_RETRY):
    sentence = re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text.strip(), maxsplit=1)[0]
    if not any(char.isalpha() for char in sentence):
        return retry
    if len(sentence.split()) > 24:
        clause = re.split(r"[,;:]", sentence, maxsplit=1)[0]
        if len(clause.split()) <= 24:
            return clause.rstrip(".!?") + "."
    return sentence


class WakeAssistant:
    def __init__(self, config, camera, audio, *, microphone=None, speech=None, provider=None,
                 detector=None, navigation=None, exclusive_vision=None, asr: ASRProvider | None = None,
                 tts: TTSProvider | None = None, report=None):
        self.camera, self.audio = camera, audio
        self.microphone = microphone if microphone is not None else self._microphone(config.get("microphone", "default"))
        self.models = Path(config.get("speech_models_dir", "voice/models"))
        self._speech, self._provider = speech, provider
        self.asr, self.tts = asr, tts
        self.report = report
        self.mode = config.get("mode", "ollama")
        self.question_language = config.get("question_language", "en")
        self.answer_language = config.get("answer_language", "en")
        if self.question_language not in TEXT or self.answer_language not in TEXT:
            raise ValueError("Assistant question_language and answer_language must be en or zh")
        self.vosk_model, self.piper_voice = config.get("vosk_model"), config.get("piper_voice")
        self.cloud = self.mode == "psyche_grok"
        self.exclusive_vision = None if self.cloud else exclusive_vision
        if self.cloud and provider is None:
            raise ValueError("Psyche vision provider has not been configured")
        self.ollama_port = config.get("ollama_port", 11434)
        self.request_timeout = config.get("request_timeout_s", 20)
        self.navigation = navigation if navigation is not None else MockNavigationAdapter()
        # In cloud mode K2 talks with Grok to choose a place. Default on for a Mandarin assistant.
        self.dialogue = None
        if (self.cloud and hasattr(self.navigation, "find_places")
                and config.get("destination_dialogue", self.answer_language == "zh")):
            from .destination_dialogue import DestinationDialogue

            self.dialogue = DestinationDialogue(provider, self.navigation)
        self.wake_enabled = not self.cloud and bool(config.get("wake_enabled", False))
        self.dashboard_test = not self.cloud and bool(config.get("dashboard_wake_test", False))
        self.ptt_gpio = config.get("ptt_gpio")
        if self.ptt_gpio is not None and (type(self.ptt_gpio) is not int or not 0 <= self.ptt_gpio <= 27):
            raise ValueError("ptt_gpio must be a Raspberry Pi BCM GPIO number")
        self.gpio_button = None
        self.command_timeout = float(config.get("max_record_s", 8))
        self.max_age = float(config.get("max_scene_age_s", 12))
        self.silence_level = float(config.get("silence_rms", 220))
        # Optional sherpa-onnx speech stack: SenseVoice recognition and Silero end-of-speech detection.
        self.endpoint = None
        if config.get("asr_backend", "vosk") == "sensevoice":
            from .sherpa_speech import SenseVoiceRecognizer, SpeechEndpoint

            directory = Path(config.get("asr_model_dir", self.models / "sense-voice"))
            if self.asr is None:
                self.asr = SenseVoiceRecognizer(directory, self.question_language,
                                                int(config.get("asr_threads", 2)))
            if config.get("auto_endpoint", True):
                self.endpoint = SpeechEndpoint(directory / "silero_vad.onnx",
                                               float(config.get("endpoint_silence_s", 0.8)))
            threading.Thread(target=self._warm_up, daemon=True, name="cane-speech-warmup").start()
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
        self.button_at = float("-inf")

    @cached_property
    def detector(self):
        return self._detector if self._detector is not None else WakeDetector(
            self.models / "vosk-model-small-ko-0.22", lambda: self.speech.recognizer_model, self.confidence)

    @cached_property
    def speech(self):
        if self._speech is not None:
            return self._speech
        from .local_speech import LocalSpeech

        return LocalSpeech(self.models, question_language=self.question_language,
                           answer_language=self.answer_language, vosk_model=self.vosk_model,
                           piper_voice=self.piper_voice)

    @cached_property
    def provider(self):
        if self._provider is not None:
            return self._provider
        from .ollama_vision import InferenceSettings, OllamaVisionProvider

        return OllamaVisionProvider(
            self.ollama_port, timeout=self.request_timeout, threads=3, settings=InferenceSettings(prompt="grounded"))

    @staticmethod
    def _microphone(choice):
        @contextmanager
        def open_stream():
            from .voice_audio import local_audio, select_endpoint

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
            return {"state": self.state, "error": self.error, "mode": self.mode, "request_id": self.request_id,
                    "wake_enabled": self.wake_enabled, "wake_phrase": DISPLAY_PHRASE,
                    "dashboard_wake_test": self.dashboard_test, "recent_states": list(self.recent),
                    "demo_exclusive_vision": self.exclusive_vision is not None,
                    "ptt_gpio_unavailable": self.ptt_gpio is not None and self.gpio_button is None,
                    "frame_id": self.frame_id, "answer": self.answer if fresh else "",
                    "answer_language": self.answer_language, "question_language": self.question_language,
                    "heard": getattr(self, "heard", ""),
                    "destination_dialogue": self.dialogue.status() if self.dialogue else None}

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

    def toggle(self, channel):
        """Accept one Pico tap at a time. The worker owns the microphone."""
        now = time.monotonic()
        with self.lock:
            if (not self.cloud or channel not in TOGGLE_CHANNELS or self.stop.is_set()
                    or now - self.button_at < .25):
                return False
            if self.trigger == channel and self.state == "LISTENING_FOR_COMMAND" and not self.released:
                self.released = True
            elif self.trigger is None and self.state == "PASSIVE_LISTENING":
                self._activate(channel)
            else:
                return False
            self.button_at = now
            return True

    def cancel_navigation_recording(self):
        with self.lock:
            if self.trigger == "navigation_button":
                self.released = self.cancelled = True

    def test_wake(self):
        if not self.dashboard_test:
            raise AssistantError("test_disabled")
        return self._activate("wake")

    def release(self, request_id, *, cancelled=False):
        with self.lock:
            if not request_id or request_id != self.request_id or self.trigger != "ptt" or self.cancelled:
                return False
            if self.released and not cancelled:
                return False
            self.released, self.cancelled = True, cancelled
            clip = self.clip if cancelled else None
            if cancelled:
                self.answer = ""
            if clip:
                self.playback_done.set()
        if clip:
            self.audio.cancel_assistant(clip)
        return True

    def valid(self, event_id, now):
        with self.lock:
            return not (self.stop.is_set() or self.cancelled) and event_id == self.clip

    def delivery(self, event_id, action, now):
        if action in {"completed", "interrupted", "expired", "invalid", "replaced", "failed", "disabled", "rejected"}:
            with self.lock:
                if event_id == self.clip:
                    self.playback_action = action
                    self.playback_done.set()

    def _speak(self, text, *, navigation=False, captured_at=None, next_state="SPEAKING"):
        from .voice_audio import read_wave

        if self.stop.is_set() or self.cancelled:
            return False
        synthesize = (self.tts or self.speech).synthesize
        samples, rate, channels = read_wave(self._timed("tts", "piper", synthesize, text))
        if channels != 1 or not samples:
            raise AssistantError("tts_unavailable")
        return self._play_pcm(samples, rate, navigation=navigation, captured_at=captured_at, next_state=next_state)

    def _play_pcm(self, samples, rate, *, navigation=False, captured_at=None, next_state="SPEAKING"):
        if self.stop.is_set():
            return False
        now = time.monotonic()
        if captured_at is not None and now - captured_at >= self.max_age:
            raise AssistantError("scene_expired")
        event_id = "assistant:" + uuid.uuid4().hex
        with self.lock:
            if self.stop.is_set() or self.cancelled:
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
            return not self.cancelled and self.playback_action == "completed"
        finally:
            with self.lock:
                self.clip = None

    def _capture(self, mic, *, ptt=False):
        import numpy as np

        end = time.monotonic() + self.command_timeout
        pcm = bytearray()
        heard = False
        silent = 0
        overlap = ptt and self.audio.busy()
        # With a speech endpoint, a K2 or K4 turn also ends by itself when the user stops talking.
        endpoint = self.endpoint if ptt and self.trigger in TOGGLE_CHANNELS else None
        ended = False
        if endpoint is not None:
            endpoint.reset()
        while not self.stop.is_set() and time.monotonic() < end and not (ptt and self.released):
            if self.cancelled:
                return b""
            block = mic(0.2)
            if not block:
                continue
            if self.audio.busy():
                # A warning can enter the microphone. Discard the incomplete utterance.
                overlap = overlap or ptt
                pcm.clear()
                heard, silent = False, 0
                if endpoint is not None:
                    endpoint.reset()
                continue
            pcm.extend(block)
            if ptt:
                if endpoint is not None and endpoint.feed(block):
                    ended = True
                    break
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
            if not (self.released or ended):
                if endpoint is None:
                    raise AssistantError("recording_limit")
                # Time ran out. Keep what was said, or report that nothing was heard.
                return bytes(pcm) if endpoint.heard and not (self.cancelled or overlap) else b""
            return bytes(pcm) if not (self.cancelled or overlap) else b""
        return bytes(pcm) if heard else b""

    def _activation(self):
        while not self.stop.is_set():
            if not self.wake_enabled and not self.trigger:
                self.trigger_ready.wait()
                continue
            if self.trigger == "wake":
                return "wake", None
            if self.trigger in TOGGLE_CHANNELS:
                kind = self.trigger
                if not self._acknowledge():
                    return None, None
                with self.microphone() as mic:
                    self._state("LISTENING_FOR_COMMAND")
                    return kind, self._capture(mic, ptt=True)
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

    def _warm_up(self):
        """Load the speech models at start so the first K2 turn is not slow."""
        try:
            getattr(self.asr, "recognizer", None)
            self.speech.voice
        except Exception:
            LOG.warning("Speech warm-up failed", exc_info=True)

    def _acknowledge(self):
        """Play the K4/K2 cue. The user starts speaking only after hearing it."""
        deadline = time.monotonic() + ACK_WAIT_S
        while not self.stop.is_set() and not self.cancelled:
            if self._play_pcm(ACK, 16000, next_state="ACKNOWLEDGING"):
                return not self.cancelled
            # Rejected or interrupted by a higher-priority warning. Wait for the speaker.
            if time.monotonic() >= deadline:
                return False
            self.stop.wait(ACK_RETRY_S)
        return False

    def _announce_failure(self, error):
        """Tell the user out loud that a cloud turn failed. Local alerts keep priority."""
        if not self.cloud or self.stop.is_set() or self.cancelled:
            return
        if error in {"audio_disabled", "busy", "tts_unavailable"}:
            return
        try:
            failures = ZH_FAILURE_SPEECH if self.answer_language == "zh" else CLOUD_FAILURE_SPEECH
            self._speak(failures.get(error, self._text("service_unavailable")), next_state="ERROR")
        except Exception:
            LOG.warning("Could not announce assistant failure %s", error, exc_info=True)

    def _text(self, key):
        return TEXT[self.answer_language][key]

    def _transcribe(self, pcm):
        self._state("TRANSCRIBING")
        pcm = speech_only_pcm(pcm, self.silence_level)
        return self._timed("asr", "vosk", (self.asr or self.speech).transcribe, pcm).strip() if pcm else ""

    def _timed(self, capability, provider, call, value):
        if not self.report:
            return call(value)
        started = time.monotonic()
        success = False
        try:
            result = call(value)
            success = bool(result)
            return result
        finally:
            self.report("voice_phase", request_id=self.request_id, capability=capability, provider=provider,
                        success=success, latency_s=round(time.monotonic()-started, 3))

    def _turn(self, kind, pcm):
        if self.cancelled:
            return
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
        self.heard = question  # shown in /assistant/status for bench checks
        if self.stop.is_set() or self.cancelled:
            return
        if not question:
            # A warning during recording discards the clip. In cloud mode say so instead of going silent.
            if self.cloud:
                self._speak(self._text("not_caught"))
            return
        if kind == "navigation_button":
            if self.dialogue is None:
                self.navigation.submit_destination(question)
                return
            self._state("PROCESSING")
            answer = self.dialogue.handle(question, request_id=self.request_id)
            if answer and self._speak(answer, navigation=True):
                with self.lock:
                    if not self.cancelled:
                        self.answer = answer
            return
        self._state("ROUTING_COMMAND")
        action, destination = route_command(question)
        if action == "visual_safety":
            answer = self._text("visual_safety")
        elif action == "vision":
            self._state("PROCESSING")
            answer = self._visual_answer(question)
        elif self.cloud:
            answer = self._text("use_navigation_button")
        else:
            answer = self._navigate(action, destination)
        if answer and self._speak(answer, navigation=action not in {"vision", "visual_safety"},
                                  captured_at=self.captured_at):
            with self.lock:
                if not self.cancelled:
                    self.answer = answer

    def _visual_answer(self, question):
        try:
            jpeg, self.frame_id, self.captured_at = shared_jpeg(self.camera)
        except AssistantError as exc:
            if str(exc) == "camera_quality":
                return self._text("vision_retry")
            if str(exc) == "camera_unavailable":
                return self._text("camera_unavailable")
            raise
        if self.cloud:
            if self.stop.is_set() or self.cancelled:
                return ""
            answer = self.provider.describe(question, jpeg, self.answer_language, request_id=self.request_id)
            if self.stop.is_set() or self.cancelled:
                return ""
            if time.monotonic() - self.captured_at >= self.max_age:
                raise AssistantError("scene_expired")
            return short_visual_answer(answer, self._text("vision_retry"))
        from PIL import Image

        floor_question = re.search(r"\b(?:floor|ground)\b", question, re.I)
        object_question = re.match(r"^what(?: is|'s) (?:on|under|beside|next to) (?:the|my|this)\b", question, re.I)
        question = question[0].upper() + question[1:]
        if question[-1] not in ".?!":
            first = question.split()[0].split("'")[0].casefold()
            question += "?" if first in {"what", "where", "who", "how", "which", "is", "are", "can", "could",
                                         "should", "do", "does"} else "."
        if object_question:
            question += " Give only the broad object category, without guessing the subtype."
        with Image.open(io.BytesIO(jpeg)) as frame:
            if floor_question and foreground_is_dark(frame):
                return "It's too dark to make out what's on the floor."
            if self.stop.is_set() or self.cancelled:
                return ""
            if self.exclusive_vision and not self._speak("Stand still. Camera alerts will pause."):
                return ""
            with self.exclusive_vision() if self.exclusive_vision else nullcontext():
                if self.stop.is_set() or self.cancelled:
                    return ""
                if self.exclusive_vision:
                    self._state("PROCESSING_EXCLUSIVE")
                result = self.provider.answer_question(question, frame)
            if self.exclusive_vision:
                self._state("PROCESSING")
        if self.stop.is_set() or self.cancelled:
            return ""
        if not result.success:
            raise AssistantError("provider_error")
        return short_visual_answer(result.answer)

    def _navigate(self, action, destination):
        if action == "unsupported_navigation":
            return "Try saying navigate to, followed by the destination."
        if action == "plan":
            if not self._speak(f"Do you want to navigate to {destination}?", navigation=True,
                               next_state="WAITING_FOR_CONFIRMATION"):
                return ""
            with self.microphone() as mic:
                text = self._capture(mic)
            if self.stop.is_set() or self.cancelled:
                return ""
            if not confirmed(self._transcribe(text)):
                return "Navigation cancelled."
            if self.stop.is_set() or self.cancelled:
                return ""
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
                self._announce_failure(str(exc))
                self._state("ERROR", str(exc))
                self.stop.wait(0.6)
            except ImportError:
                self._state("ERROR", "asr_unavailable")
                self.stop.wait(1.5)
            except Exception:
                LOG.exception("Local voice assistant failed")
                self._announce_failure("assistant_error")
                self._state("ERROR", "assistant_error")
                self.stop.wait(1.5)
            finally:
                with self.lock:
                    self.trigger = self.request_id = None
                    self.trigger_ready.clear()
                    if not self.stop.is_set():
                        # Preserve the last error until a successfully opened turn starts.
                        self._state("PASSIVE_LISTENING", self.error)
