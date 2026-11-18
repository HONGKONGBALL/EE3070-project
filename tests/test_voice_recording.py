"""Recorder selection and unavailable ASR are explicit boundary outcomes."""

from __future__ import annotations

import subprocess
import wave
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest
from cane.app import CaneApp
from cane.voice import asr, recorder

if TYPE_CHECKING:
    from .conftest import FakeAudio

pytest_plugins = ("test_app",)


def test_missing_vosk_model_is_unavailable_without_importing_vosk(tmp_path):
    # Given no offline model installed.
    # When the recognizer is requested.
    # Then the unavailable reason identifies the missing model rather than fake text.
    with pytest.raises(asr.ASRUnavailable, match="model"):
        asr.VoskRecognizer(tmp_path, "cmn")


def test_mac_recorder_reports_missing_backend(monkeypatch, tmp_path):
    # Given macOS without ffmpeg or sox.
    monkeypatch.setattr(recorder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(recorder, "_record_sounddevice", lambda *args: False)
    monkeypatch.setattr(recorder.shutil, "which", lambda name: None)
    # When recording is requested.
    # Then callers receive an actionable unavailable error.
    with pytest.raises(recorder.RecorderUnavailable, match="ffmpeg|sox"):
        recorder.record_wav(tmp_path / "clip.wav", 1)


def test_mac_ffmpeg_records_vosk_compatible_audio(monkeypatch, tmp_path):
    # Given ffmpeg installed and a narrow process seam for the real mic command.
    calls = []
    monkeypatch.setattr(recorder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(recorder, "_record_sounddevice", lambda *args: False)
    monkeypatch.setattr(recorder.shutil, "which", lambda name: "/usr/local/bin/ffmpeg" if name == "ffmpeg" else None)

    def process(command, **kwargs):
        calls.append(command)
        with wave.open(command[-1], "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\0\0" * 160)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(recorder.subprocess, "run", process)
    # When a short microphone recording completes.
    result = recorder.record_wav(tmp_path / "clip.wav", 0.5)
    # Then the output exists and the native macOS audio input was selected.
    assert result.is_file()
    assert "avfoundation" in calls[0]
    assert ":0" in calls[0]
    assert "pcm_s16le" in calls[0]


def test_waits_for_queued_cue_then_records_before_asr(
    app: CaneApp, audio: FakeAudio, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Path] = []
    waits: list[float] = []
    assert app.say("voice_listening")
    assert "voice_listening" in app.arbiter.pending()

    def advance_speaker(seconds: float) -> bool:
        waits.append(seconds)
        if len(waits) == 2:
            _ = app.arbiter.tick()
        else:
            audio.finish()
        return False

    def capture(target: Path, _seconds: float, _device: str) -> Path:
        assert ["voice_listening"] in audio.played and not audio.busy()
        assert app.voice_wake.microphone.locked()
        _ = target.write_bytes(b"captured utterance")
        captured.append(target)
        return target

    class Recognizer:
        def __init__(self, _model_dir: str, _lang: str, _phrases: list[str]) -> None:
            assert captured and captured[-1].read_bytes() == b"captured utterance"
            assert not app.voice_wake.microphone.locked()

        def transcribe(self, wav_path: Path) -> str:
            assert wav_path == captured[-1]
            assert not app.voice_wake.microphone.locked()
            return "香港城市大学"

    monkeypatch.setattr(app._stop, "wait", advance_speaker)
    monkeypatch.setattr(recorder, "record_wav", capture)
    monkeypatch.setattr(asr, "VoskRecognizer", Recognizer)

    assert app._record_and_transcribe() == "香港城市大学"
    assert len(waits) == 3
    assert not captured[0].exists()


def test_manual_recording_stops_on_request_and_then_transcribes(
    app: CaneApp, audio: FakeAudio, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.cfg.voice.manual_stop = True
    monkeypatch.setattr(app, "_voice_unavailable_reason", lambda: None)
    monkeypatch.setattr(audio, "busy", lambda: False)

    def capture(target: Path, stop: Event, _device: str) -> Path:
        assert app.voice_recording and app.voice_record_stop is stop
        assert not stop.is_set()
        assert app.stop_voice_input()
        assert stop.is_set()
        assert not app.stop_voice_input()
        _ = target.write_bytes(b"completed manual utterance")
        return target

    class Recognizer:
        def __init__(self, _model_dir: str, _lang: str, _phrases: list[str]) -> None:
            assert not app.voice_recording
            assert app.voice_record_stop is None

        def transcribe(self, target: Path) -> str:
            assert target.read_bytes() == b"completed manual utterance"
            return "香港城市大学"

    monkeypatch.setattr(recorder, "record_until_stopped", capture)
    monkeypatch.setattr(asr, "VoskRecognizer", Recognizer)

    app.start_voice_input()
    app._jobs.get_nowait()()

    assert not app.listening and not app.voice_recording
    assert app.pending_dest is not None and app.pending_dest.name == "香港城市大学"


def test_stop_during_prompt_never_starts_manual_capture(
    app: CaneApp, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.cfg.voice.manual_stop = True
    workers: list[Callable[[], None]] = []

    def queue_worker(worker: Callable[[], None], _name: str) -> None:
        workers.append(worker)

    def fail_capture(_target: Path, _stop: Event, _device: str) -> Path:
        pytest.fail("Capture started after the stop request")

    monkeypatch.setattr(app, "_voice_unavailable_reason", lambda: None)
    monkeypatch.setattr(app, "_spawn", queue_worker)
    monkeypatch.setattr(recorder, "record_until_stopped", fail_capture)

    app.start_voice_input()
    assert app.listening and not app.voice_recording
    assert app.stop_voice_input()
    workers[0]()
    app._jobs.get_nowait()()

    assert not app.listening and app.voice_record_stop is None


def test_destination_text_supersedes_manual_recording(
    app: CaneApp, audio: FakeAudio, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.cfg.voice.manual_stop = True
    monkeypatch.setattr(app, "_voice_unavailable_reason", lambda: None)
    monkeypatch.setattr(audio, "busy", lambda: False)
    recordings: list[Path] = []

    def capture(target: Path, stop: Event, _device: str) -> Path:
        _ = app.on_destination_text("香港城市大学")
        assert stop.is_set()
        _ = target.write_bytes(b"superseded utterance")
        recordings.append(target)
        return target

    def recognize(*_args: object) -> None:
        pytest.fail("The text input already supplied the destination")

    monkeypatch.setattr(recorder, "record_until_stopped", capture)
    monkeypatch.setattr(asr, "VoskRecognizer", recognize)

    app.start_voice_input()
    app._jobs.get_nowait()()

    assert not recordings[0].exists()
    assert not app.listening and app.voice_record_stop is None
    assert app.pending_dest is not None and app.pending_dest.name == "香港城市大学"
