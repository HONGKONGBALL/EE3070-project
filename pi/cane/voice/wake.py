"""On-device phrase detection for a Mac or Raspberry Pi microphone."""

from __future__ import annotations

import json
import os
import platform
import selectors
import shutil
import subprocess
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

from .asr import VOSK_MODELS, ASRUnavailable, VoskVocabulary, compile_grammar
from .recorder import RecorderUnavailable

if TYPE_CHECKING:
    from _thread import LockType
    from collections.abc import Callable

DEFAULT_WAKE_PHRASE: Final = "你好导航"
_SAMPLE_RATE: Final = 16000
_CHUNK_BYTES: Final = 3200
_CAPTURE_CHUNKS: Final = 8
_POLL_SECONDS: Final = 0.1
_WORD_CONFIDENCE: Final = 0.85
_PROCESS_GRACE_SECONDS: Final = 0.5
_JsonValue: TypeAlias = (
    str | int | float | bool | None | list["_JsonValue"] | dict[str, "_JsonValue"]
)
_load_json: Final[Callable[[str], _JsonValue]] = json.loads


class _WakeDecoder(Protocol):
    def AcceptWaveform(self, data: bytes, /) -> int: ...

    def Result(self) -> str: ...

    def SetWords(self, enabled: bool, /) -> None: ...


class _AudioBuffer(Protocol):
    def __getitem__(self, key: slice, /) -> bytes: ...


class _AudioClock(Protocol):
    @property
    def inputBufferAdcTime(self) -> float: ...


class _AudioStatus(Protocol):
    def __bool__(self) -> bool: ...


def _matches_wake_result(payload: str, expected_phrase: str) -> bool:
    """Require all tokens in one completed utterance to match with confidence."""
    try:
        result: _JsonValue = _load_json(payload)
    except json.JSONDecodeError:
        return False
    expected = expected_phrase.split()
    match result:  # noqa: MATCH_OK - Vosk result shape.
        case {"text": str(text), "result": list(words)}:
            if not expected or text.split() != expected or len(words) != len(expected):
                return False
            for word, token in zip(words, expected, strict=True):
                match word:  # noqa: MATCH_OK - Vosk word shape.
                    case {
                        "word": str(spoken),
                        "conf": int(confidence) | float(confidence),
                    }:
                        if spoken != token or not _WORD_CONFIDENCE <= confidence <= 1:
                            return False
                    case _:
                        return False
            return True
        case _:
            return False


class WakeWordListener:
    """Load a local Vosk model and listen for one complete wake phrase."""

    def __init__(
        self, model_dir: str | Path, lang: str, phrase: str = DEFAULT_WAKE_PHRASE
    ) -> None:
        """Compile the phrase against the configured model's vocabulary."""
        model_name = VOSK_MODELS.get(lang)
        if model_name is None:
            raise ASRUnavailable(f"unsupported local wake language: {lang}")
        path = Path(model_dir) / model_name
        if not path.is_dir():
            raise ASRUnavailable(f"Vosk model not found: {path}")
        try:
            from vosk import KaldiRecognizer, Model
        except ImportError as exc:
            raise ASRUnavailable("Vosk dependency is not installed") from exc
        try:
            model = Model(str(path))
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - Vosk model boundary.
            raise ASRUnavailable(f"Vosk cannot load model: {path}") from exc
        grammar = compile_grammar([phrase], lang, model)
        if grammar is None:
            raise ASRUnavailable(
                f"wake phrase is outside the local Vosk vocabulary: {phrase}"
            )
        grammar_entries: _JsonValue = _load_json(grammar)
        match grammar_entries:  # noqa: MATCH_OK - Vosk grammar shape.
            case [str(first_phrase), *_]:
                self._expected_phrase: str = first_phrase
            case _:
                raise ASRUnavailable("Vosk wake grammar has no phrase")

        make_decoder: Callable[[VoskVocabulary, int, str], _WakeDecoder] = (
            KaldiRecognizer
        )

        def new_decoder() -> _WakeDecoder:
            try:
                decoder = make_decoder(model, _SAMPLE_RATE, grammar)
                decoder.SetWords(True)
            except Exception as exc:  # noqa: BROAD_EXCEPT_OK - Vosk decoder boundary.
                raise ASRUnavailable("Vosk cannot initialize the wake decoder") from exc
            return decoder

        self._new_decoder: Callable[[], _WakeDecoder] = new_decoder

    def listen(
        self,
        *,
        enabled: bool = False,
        device: str,
        stop: Event,
        microphone: LockType,
        on_wake: Callable[[], None],
    ) -> bool:
        """Listen when opted in, close capture, then call the wake callback once."""
        if not enabled or stop.is_set() or not microphone.acquire(blocking=False):
            return False
        try:
            if stop.is_set():
                return False
            decoded = self._wait_for_phrase(device, stop)
        finally:
            microphone.release()
        if decoded and not stop.is_set():
            on_wake()
            return True
        return False

    def _wait_for_phrase(self, device: str, stop: Event) -> bool:
        if platform.system() == "Darwin":
            return self._wait_for_macos_phrase(device, stop)
        executable = shutil.which("arecord")
        if executable is None:
            raise RecorderUnavailable("arecord is required for local wake word capture")
        decoder = self._new_decoder()
        command = [
            executable,
            "-q",
            "-D",
            device,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-r",
            str(_SAMPLE_RATE),
        ]
        try:
            with subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ) as capture:
                try:
                    pipe = capture.stdout
                    if pipe is None:
                        raise RecorderUnavailable(
                            "microphone capture pipe is unavailable"
                        )
                    with selectors.DefaultSelector() as selector:
                        _ = selector.register(pipe, selectors.EVENT_READ)
                        while not stop.is_set():
                            if not selector.select(timeout=_POLL_SECONDS):
                                continue
                            chunk = os.read(pipe.fileno(), _CHUNK_BYTES)
                            if not chunk:
                                if stop.is_set():
                                    return False
                                raise RecorderUnavailable(
                                    "microphone capture ended before a wake phrase"
                                )
                            if self._heard_phrase(decoder, chunk):
                                return True
                    return False
                finally:
                    if capture.poll() is None:
                        capture.terminate()
                        try:
                            _ = capture.wait(timeout=_PROCESS_GRACE_SECONDS)
                        except subprocess.TimeoutExpired:
                            capture.kill()
                            _ = capture.wait()
        except OSError as exc:
            raise RecorderUnavailable(f"microphone capture failed: {exc}") from exc

    def _heard_phrase(self, decoder: _WakeDecoder, chunk: bytes) -> bool:
        try:
            result = decoder.Result() if decoder.AcceptWaveform(chunk) else None
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - Vosk stream boundary.
            raise ASRUnavailable("Vosk could not decode the audio stream") from exc
        return result is not None and _matches_wake_result(
            result, self._expected_phrase
        )

    def _wait_for_macos_phrase(self, device: str, stop: Event) -> bool:
        """Feed bounded PortAudio PCM blocks to Vosk until stop or a match."""
        try:
            import sounddevice
        except (ImportError, OSError) as exc:
            raise RecorderUnavailable(
                "sounddevice is required for macOS wake capture"
            ) from exc
        decoder = self._new_decoder()
        chunks: Queue[bytes] = Queue(maxsize=_CAPTURE_CHUNKS)
        failed = Event()

        def capture(
            data: _AudioBuffer, _frames: int, _clock: _AudioClock, status: _AudioStatus
        ) -> None:
            if status:
                failed.set()
            elif not failed.is_set() and not stop.is_set():
                try:
                    chunks.put_nowait(data[:])
                except Full:
                    failed.set()

        try:
            with sounddevice.RawInputStream(
                samplerate=_SAMPLE_RATE,
                blocksize=_CHUNK_BYTES // 2,
                channels=1,
                dtype="int16",
                device=None if device == "default" else device,
                callback=capture,
                finished_callback=failed.set,
            ):
                while not stop.is_set():
                    if failed.is_set():
                        raise RecorderUnavailable(
                            "macOS wake audio stream stopped or overflowed"
                        )
                    try:
                        chunk = chunks.get(timeout=_POLL_SECONDS)
                    except Empty:
                        continue
                    if (
                        not stop.is_set()
                        and self._heard_phrase(decoder, chunk)
                        and not failed.is_set()
                    ):
                        return True
        except (sounddevice.PortAudioError, OSError, ValueError) as exc:
            raise RecorderUnavailable(
                f"macOS microphone capture failed: {exc}"
            ) from exc
        return False
