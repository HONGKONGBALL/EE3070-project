"""Exercise the integrated assistant with WAV fixtures and a timed output sink."""

import argparse
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace
import wave

import cv2

from cane.vision.speech import Speech
from cane.vision.voice_commands import MockNavigationAdapter
from cane.vision.wake_assistant import WakeAssistant


class FixtureMicrophone:
    def __init__(self, utterances):
        self.utterances = iter(utterances)
        self.active = 0
        self.utterance_sent = Event()

    @contextmanager
    def open(self):
        assert self.active == 0
        self.active += 1
        self.utterance_sent.clear()
        utterance = next(self.utterances, b"")
        audio = utterance + bytes(40_000)
        blocks = iter(audio[i:i + 3200] for i in range(0, len(audio), 3200))
        transferred = 0

        def read(timeout):
            nonlocal transferred
            block = next(blocks, b"")
            time.sleep(0.007 if block else min(timeout, 0.05))
            transferred += len(block)
            if transferred >= len(utterance):
                self.utterance_sent.set()
            return block

        try:
            yield read
        finally:
            self.active -= 1


class TimedSink:
    def __init__(self, record, microphone):
        self.record = record
        self.microphone = microphone
        self.enabled = True
        self.entries = []
        self.current = None
        self.playback_id = None

    def play(self, clips, text=None, *, context=None, pcm=None):
        if pcm:
            assert self.microphone.active == 0
        entry = (clips[0], dict(context or {}), pcm)
        self.entries.append(entry)
        self.current = entry
        self.ends = time.monotonic() + (0.25 if pcm else 0.13)
        self.record("audio_start", **entry[1])

    def busy(self):
        if self.current and time.monotonic() >= self.ends:
            entry, self.current = self.current, None
            self.record("audio_done", **entry[1])
        return self.current is not None

    def stop(self):
        if self.current:
            entry, self.current = self.current, None
            self.record("audio_interrupted", **entry[1])


class TracedNavigation(MockNavigationAdapter):
    def __init__(self):
        self.calls = []

    def plan_route(self, destination):
        self.calls.append(["plan", destination])
        return super().plan_route(destination)

    def get_next_instruction(self):
        self.calls.append(["next"])
        return super().get_next_instruction()

    def stop_navigation(self):
        self.calls.append(["stop"])
        return super().stop_navigation()


class TracedSpeech:
    def __init__(self, speech):
        self.speech = speech
        self.timeline = []

    @property
    def recognizer_model(self):
        return self.speech.recognizer_model

    def synthesize(self, text):
        started = time.perf_counter()
        response = self.speech.synthesize(text)
        self.timeline.append(["tts", text, round(time.perf_counter() - started, 3)])
        return response

    def transcribe(self, pcm):
        started = time.perf_counter()
        text = self.speech.transcribe(pcm)
        self.timeline.append(["asr", text, round(time.perf_counter() - started, 3)])
        return text


def write_audio(path, rate, pcm):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(pcm)


def synthesize_input(speech, text, settings):
    from voice_audio import read_wave

    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as stream:
            speech.voice.synthesize_wav(text, stream, syn_config=settings)
        return read_wave(buffer.getvalue())[0]


def main():
    from gpiozero import Device
    from gpiozero.pins.mock import MockFactory
    from ollama_vision import InferenceSettings, OllamaVisionProvider
    from piper import SynthesisConfig
    from voice_audio import read_wave
    from voice_demo import LocalSpeech

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()
    source = cv2.imread(str(args.image))
    if source is None:
        raise ValueError("Could not open scene fixture")
    camera = SimpleNamespace(next_frame=lambda *_a, **_k: (source.copy(), 42, time.monotonic()))
    speech = LocalSpeech(args.models)
    provider = OllamaVisionProvider(timeout=18, settings=InferenceSettings(prompt="grounded"))
    wake = read_wave((args.fixtures / "ipa.wav").read_bytes())[0]
    input_voice = SynthesisConfig(noise_scale=0, noise_w_scale=0)
    reports = {}
    scenarios = (
        ("button-front", False, ("What's in front of me?",), True, []),
        ("button-left", False, ("What's on my left?",), True, []),
        ("button-navigation", False, ("Navigate to CityU Library.", "Yes."), False, [["plan", "CityU Library"]]),
        ("button-next-turn", False, ("What's my next turn?",), False, [["next"]]),
        ("button-stop", False, ("Stop navigation.",), False, [["stop"]]),
        ("wake-surroundings", True, (wake, "Describe my surroundings."), True, []),
        ("wake-navigation", True, (wake, "Navigate to CityU Library.", "Yes."), False, [["plan", "CityU Library"]]),
        ("decline", False, ("Navigate to CityU Library.", "No."), False, []),
        ("interrupt", False, ("What's in front of me?",), True, []),
        ("interrupt-navigation", False, ("Navigate to CityU Library.", "Yes."), False, []),
    )
    for name, hands_free, utterances, use_ollama, expected_navigation in scenarios:
        pcm = [part if isinstance(part, bytes) else synthesize_input(speech, part, input_voice)
               for part in utterances]
        microphone = FixtureMicrophone(pcm)
        engine = TracedSpeech(speech)
        navigation = TracedNavigation()
        ollama_calls = []

        def question(text, image):
            ollama_calls.append(text)
            return provider.answer_question(text, image)

        audio = Speech(args.fixtures, True, ordinary_interval_s=0)
        audio.sink = sink = TimedSink(audio._record_audio, microphone)
        pin_factory = Device.pin_factory = MockFactory()
        assistant = WakeAssistant({"max_record_s": 4, "max_scene_age_s": 25, "speech_models_dir": args.models,
                                   "wake_enabled": hands_free, "ptt_gpio": None if hands_free else 17},
                                  camera, audio, microphone=microphone.open, speech=engine,
                                  provider=SimpleNamespace(answer_question=question), navigation=navigation)
        audio.validator = lambda req: not req.priority.is_assistant or assistant.valid(req.event_id, time.monotonic())
        audio.on_delivery = assistant.delivery
        assistant.start()
        assert not assistant.status()["ptt_gpio_unavailable"]
        pin = None if hands_free else pin_factory.pin(17)
        interrupted = False
        held_press = pin is not None
        if pin:
            pin.drive_low()
        end = time.monotonic() + 30
        try:
            while time.monotonic() < end:
                audio.tick()
                status = assistant.status()
                if held_press and status["state"] == "LISTENING_FOR_COMMAND":
                    assert microphone.utterance_sent.wait(2), name
                    time.sleep(.05)
                    assert not engine.timeline and not ollama_calls and not navigation.calls
                    pin.drive_high()
                    held_press = False
                if name.startswith("interrupt") and sink.entries and sink.busy() and not interrupted:
                    if name == "interrupt-navigation":
                        alert, priority = "crowd_ahead", "INFO"
                    else:
                        alert, priority = "stairs_ahead", "CRITICAL"
                    audio.submit({"phrase_id": alert, "event_id": "test-warning", "priority": priority})
                    interrupted = True
                if status["state"] == "PASSIVE_LISTENING" and "COOLDOWN" in status["recent_states"]:
                    break
                if status["error"]:
                    raise RuntimeError(status)
                time.sleep(.02)
            else:
                raise TimeoutError(f"{name}: {assistant.status()}")
        finally:
            assistant.close()
        for index, (_phrase, _context, clip) in enumerate(sink.entries):
            if clip:
                write_audio(args.fixtures / f"{name}-{index}.wav", *clip)
        replies = [row[1] for row in engine.timeline if row[0] == "tts"]
        assert bool(replies and replies[0] == "Yes?") == hands_free
        assert len(ollama_calls) == int(use_ollama), (name, engine.timeline)
        assert navigation.calls == expected_navigation, (name, navigation.calls, engine.timeline)
        if use_ollama:
            assert status["frame_id"] == 42
        if name in {"button-navigation", "wake-navigation"}:
            assert replies[-2] == "Do you want to navigate to CityU Library?"
            assert replies[-1] == "Navigation is unavailable."
        if name == "decline":
            assert replies[-1] == "Navigation cancelled."
        if name.startswith("interrupt"):
            assert interrupted and [entry[0] for entry in sink.entries] == ["assistant_reply", alert]
            assert not status["answer"]
        reports[name] = {"activation": "wake recording" if hands_free else "mock GPIO button",
                         "speech": engine.timeline, "navigation_calls": navigation.calls, "ollama_calls": ollama_calls,
                         "states": status["recent_states"], "warnings": [entry[0] for entry in sink.entries if not entry[2]],
                         "deliveries": list(audio.deliveries)}
        print(name, replies, flush=True)
    (args.fixtures / "flow-results.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
