from __future__ import annotations

import json
import os
import platform
import sys
from contextlib import contextmanager
from threading import Event, Lock
from types import ModuleType
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from cane.voice import wake

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.mark.parametrize(
    ("text", "confidence", "expected"),
    (
        ("你好 导航", 0.96, True),
        ("你好 导航", 0.64, False),
        ("取消 你好 导航", 0.98, False),
        ("你好 导航 [unk]", 0.95, False),
        ("确认", 0.99, False),
    ),
)
def test_wake_matches_a_whole_confident_phrase(
    text: str, confidence: float, expected: bool
) -> None:
    payload = json.dumps(
        {
            "text": text,
            "result": [{"word": word, "conf": confidence} for word in text.split()],
        }
    )

    assert wake._matches_wake_result(payload, "你好 导航") is expected


def test_opt_out_and_cancel_do_not_acquire_the_microphone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = wake.WakeWordListener.__new__(wake.WakeWordListener)
    microphone = Lock()
    stop = Event()

    def fail() -> None:
        pytest.fail("Wake callback must wait for a complete wake phrase")

    def unexpected_capture(*args: str, **kwargs: str) -> None:
        pytest.fail("Recording started when opt-out or cancellation was active")

    monkeypatch.setattr(wake.subprocess, "Popen", unexpected_capture)
    assert not listener.listen(
        enabled=False, device="default", stop=stop, microphone=microphone, on_wake=fail
    )

    stop.set()
    assert not listener.listen(
        enabled=True, device="default", stop=stop, microphone=microphone, on_wake=fail
    )


@pytest.mark.parametrize("cancel", (False, True))
def test_capture_exits_and_releases_the_microphone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cancel: bool,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    microphone, stop = Lock(), Event()
    seen: list[int] = []
    callbacks: list[bool] = []
    source = tmp_path / "arecord"
    _ = source.write_text(
        f"#!{sys.executable}\n"
        "import os, signal\n"
        "os.write(1, os.getpid().to_bytes(8, 'little'))\n"
        "signal.pause()\n",
    )
    source.chmod(0o700)
    monkeypatch.setattr(wake.shutil, "which", lambda _: str(source))

    class FileDecoder:
        def AcceptWaveform(self, data: bytes) -> bool:
            seen.append(int.from_bytes(data, "little"))
            if cancel:
                stop.set()
            return not cancel

        def Result(self) -> str:
            return '{"text":"你好 导航","result":[{"word":"你好","conf":1},{"word":"导航","conf":1}]}'

        def SetWords(self, enabled: bool) -> None:
            pass

    listener = wake.WakeWordListener.__new__(wake.WakeWordListener)
    listener._new_decoder = FileDecoder
    listener._expected_phrase = "你好 导航"

    def begin_short_recording() -> None:
        callbacks.append(not microphone.locked())
        with pytest.raises(ProcessLookupError):
            os.kill(seen[0], 0)

    matched = listener.listen(
        enabled=True,
        device="default",
        stop=stop,
        microphone=microphone,
        on_wake=begin_short_recording,
    )

    assert matched is not cancel
    assert callbacks == ([] if cancel else [True])
    assert not microphone.locked()
    with pytest.raises(ProcessLookupError):
        os.kill(seen[0], 0)


@pytest.mark.parametrize(
    "scenario", ("matched", "cancelled", "overflow", "device_error")
)
def test_macos_capture_hands_off_or_releases_after_exit(
    monkeypatch: pytest.MonkeyPatch,
    scenario: Literal["matched", "cancelled", "overflow", "device_error"],
) -> None:
    stop, microphone = Event(), Lock()
    transitions: list[str] = []
    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    class AudioDeviceError(RuntimeError):
        """Simulate the native PortAudio error type."""

    class Decoder:
        def AcceptWaveform(self, data: bytes) -> int:
            assert data == b"\x00\x01" * 1600
            transitions.append("decode")
            return 1

        def Result(self) -> str:
            return '{"text":"你好 导航","result":[{"word":"你好","conf":1},{"word":"导航","conf":1}]}'

        def SetWords(self, _enabled: bool) -> None:
            return None

    @contextmanager
    def input_stream(
        *,
        samplerate: int,
        blocksize: int,
        channels: int,
        dtype: str,
        device: str | None,
        callback: Callable[[bytes, int, None, bool], None],
        finished_callback: Callable[[], None],
    ) -> Iterator[None]:
        assert (samplerate, blocksize, channels, dtype, device) == (
            16000,
            1600,
            1,
            "int16",
            None,
        )
        if scenario == "device_error":
            raise AudioDeviceError("unavailable microphone")
        transitions.append("opened")
        try:
            if scenario == "cancelled":
                stop.set()
            else:
                callback(b"\x00\x01" * 1600, 1600, None, scenario == "overflow")
            yield
        finally:
            transitions.append("closed")
            finished_callback()

    fake = ModuleType("sounddevice")
    fake.PortAudioError = AudioDeviceError
    fake.RawInputStream = input_stream
    monkeypatch.setitem(sys.modules, "sounddevice", fake)

    listener = wake.WakeWordListener.__new__(wake.WakeWordListener)
    listener._new_decoder = Decoder
    listener._expected_phrase = "你好 导航"

    def on_wake() -> None:
        assert not microphone.locked()
        transitions.append("wake")

    def listen() -> bool:
        return listener.listen(
            enabled=True,
            device="default",
            stop=stop,
            microphone=microphone,
            on_wake=on_wake,
        )

    match scenario:
        case "matched":
            assert listen()
            assert transitions == ["opened", "decode", "closed", "wake"]
        case "cancelled":
            assert not listen()
            assert transitions == ["opened", "closed"]
        case "overflow":
            with pytest.raises(wake.RecorderUnavailable, match="overflowed"):
                _ = listen()
            assert transitions == ["opened", "closed"]
        case "device_error":
            with pytest.raises(
                wake.RecorderUnavailable, match="unavailable microphone"
            ):
                _ = listen()
            assert transitions == []
        case unreachable:
            assert_never(unreachable)
    assert not microphone.locked()
