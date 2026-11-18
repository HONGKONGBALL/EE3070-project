"""Manual microphone capture closes on the operator's stop signal."""

import sys
import wave
from pathlib import Path
from threading import Event
from types import ModuleType, TracebackType
from typing import Self

import pytest
from cane.voice import recorder


class AudioDeviceError(RuntimeError):
    """PortAudio failure at the simulated device boundary."""


def test_manual_capture_writes_audio_until_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate = 16000
    stop = Event()
    frames = 0
    exited = False

    class Input:
        def __enter__(self) -> Self:
            return self

        def __exit__(
            self, _kind: type[BaseException] | None, _error: BaseException | None, _tb: TracebackType | None,
        ) -> None:
            nonlocal exited
            exited = True

        def read(self, count: int) -> tuple[bytes, bool]:
            nonlocal frames
            assert not stop.is_set()
            frames += count
            if frames >= 31 * rate:
                stop.set()
            return b"\x01\x01" * count, False

    def open_input(**_options: str | int | None) -> Input:
        return Input()

    backend = ModuleType("sounddevice")
    monkeypatch.setattr(backend, "RawInputStream", open_input, raising=False)
    monkeypatch.setattr(backend, "PortAudioError", AudioDeviceError, raising=False)
    monkeypatch.setitem(sys.modules, "sounddevice", backend)

    recorded = recorder.record_until_stopped(tmp_path / "clip.wav", stop, rate=rate)

    assert exited
    with wave.open(str(recorded), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, rate)
        assert audio.getnframes() == frames >= 31 * rate
        assert audio.readframes(1) == b"\x01\x01"


def test_stop_before_capture_leaves_no_recording(tmp_path: Path) -> None:
    clip = tmp_path / "clip.wav"
    _ = clip.write_bytes(b"previous audio")
    stop = Event()
    stop.set()

    with pytest.raises(recorder.RecordingStoppedEarly):
        recorder.record_until_stopped(clip, stop)

    assert not clip.exists()


def test_manual_capture_missing_backend_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    clip = tmp_path / "clip.wav"

    with pytest.raises(recorder.RecorderUnavailable, match="sounddevice"):
        recorder.record_until_stopped(clip, Event())

    assert not clip.exists()


def test_manual_capture_device_failure_removes_partial_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_once = False

    class Input:
        def __enter__(self) -> Self:
            return self

        def __exit__(
            self, _kind: type[BaseException] | None, _error: BaseException | None, _tb: TracebackType | None,
        ) -> None:
            return None

        def read(self, count: int) -> tuple[bytes, bool]:
            nonlocal read_once
            if read_once:
                raise AudioDeviceError("device disconnected")
            read_once = True
            return b"\0\0" * count, False

    def open_input(**_options: str | int | None) -> Input:
        return Input()

    backend = ModuleType("sounddevice")
    monkeypatch.setattr(backend, "RawInputStream", open_input, raising=False)
    monkeypatch.setattr(backend, "PortAudioError", AudioDeviceError, raising=False)
    monkeypatch.setitem(sys.modules, "sounddevice", backend)
    clip = tmp_path / "clip.wav"

    with pytest.raises(recorder.RecorderUnavailable, match="device disconnected"):
        recorder.record_until_stopped(clip, Event())

    assert read_once and not clip.exists()
