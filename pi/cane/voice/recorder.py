"""Stream mono PCM recordings using PortAudio or a fixed-duration recorder."""

from __future__ import annotations

import math
import platform
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from threading import Event

READ_FRAMES: Final = 1024
MANUAL_READS_PER_SECOND: Final = 20
MIN_SAMPLE_RATE: Final = 8000
MAX_SAMPLE_RATE: Final = 48000
SAMPLE_WIDTH: Final = 2


class RecorderUnavailable(RuntimeError):
    """The recording backend or microphone is unavailable; no audio was captured."""


class RecordingStoppedEarly(RecorderUnavailable):
    """The operator stopped a recording before any audio was captured."""


def _record_sounddevice(
    path: Path, seconds: float | None, device: str, rate: int, *, stop: Event | None = None,
) -> bool:
    """Write PortAudio input until the frame limit or stop signal is reached."""
    try:
        import sounddevice
    except ImportError:
        return False
    except OSError as exc:
        message = "sounddevice could not load its PortAudio backend"
        raise RecorderUnavailable(message) from exc
    selected = None if device == "default" else device
    remaining = None if seconds is None else max(1, round(seconds * rate))
    block = min(READ_FRAMES, rate // MANUAL_READS_PER_SECOND) if stop is not None else READ_FRAMES
    wrote_audio = False
    try:
        with (
            sounddevice.RawInputStream(samplerate=rate, channels=1, dtype="int16", device=selected) as stream,
            wave.open(str(path), "wb") as output,
        ):
            output.setnchannels(1)
            output.setsampwidth(SAMPLE_WIDTH)
            output.setframerate(rate)
            while (stop is None or not stop.is_set()) and (remaining is None or remaining > 0):
                count = block if remaining is None else min(block, remaining)
                data, overflow = stream.read(count)
                if overflow:
                    message = "microphone input overflowed; please retry"
                    raise RecorderUnavailable(message)
                pcm = bytes(data)
                if len(pcm) != count * SAMPLE_WIDTH:
                    message = "microphone did not produce a complete PCM block"
                    raise RecorderUnavailable(message)
                output.writeframesraw(pcm)
                wrote_audio = True
                if remaining is not None:
                    remaining -= count
            if not wrote_audio:
                message = "recording stopped before audio was captured"
                raise RecordingStoppedEarly(message)
    except RecorderUnavailable:
        path.unlink(missing_ok=True)
        raise
    except (sounddevice.PortAudioError, OSError, ValueError, wave.Error, struct.error) as exc:
        path.unlink(missing_ok=True)
        message = f"microphone recording failed: {exc}"
        raise RecorderUnavailable(message) from exc
    return True


def record_until_stopped(
    path: str | Path, stop: Event, device: str = "default", rate: int = 16000,
) -> Path:
    """Write a mono PCM WAV in short blocks until the operator signals stop."""
    if not MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE:
        message = "recording requires an 8–48 kHz sample rate"
        raise RecorderUnavailable(message)
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
    except OSError as exc:
        message = f"could not prepare microphone recording: {exc}"
        raise RecorderUnavailable(message) from exc
    if stop.is_set():
        message = "recording stopped before audio was captured"
        raise RecordingStoppedEarly(message)
    if not _record_sounddevice(target, None, device, rate, stop=stop):
        message = "install sounddevice and PortAudio for manually stopped recording"
        raise RecorderUnavailable(message)
    return target


def record_wav(path: str | Path, seconds: float, device: str = "default", rate: int = 16000) -> Path:
    """Record bounded 16-bit mono PCM; never replace failures with fake speech."""
    target = Path(path)
    if not math.isfinite(seconds) or not 0 < seconds <= 30 or not MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE:
        raise RecorderUnavailable("recording requires 0–30 seconds and an 8–48 kHz sample rate")
    target.parent.mkdir(parents=True, exist_ok=True)
    # A previous successful clip must not masquerade as this recording.
    target.unlink(missing_ok=True)
    if platform.system() == "Darwin":
        if _record_sounddevice(target, seconds, device, rate):
            return target
        ffmpeg, sox = shutil.which("ffmpeg"), shutil.which("sox")
        if ffmpeg:
            source = ":0" if device == "default" else (device if device.startswith(":") else f":{device}")
            cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "avfoundation", "-i", source,
                   "-t", str(seconds), "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(target)]
        elif sox:
            if device != "default":
                raise RecorderUnavailable("sox fallback only supports the default macOS microphone")
            cmd = [sox, "-q", "-d", "-c", "1", "-r", str(rate), "-b", "16", "-e", "signed-integer",
                   str(target), "trim", "0", str(seconds)]
        else:
            raise RecorderUnavailable("install sounddevice, ffmpeg or sox to record on macOS")
    else:
        executable = shutil.which("arecord")
        if not executable:
            raise RecorderUnavailable("arecord is not installed; use browser speech/text or install a recorder")
        cmd = [executable, "-q", "-D", device, "-f", "S16_LE", "-c", "1", "-r", str(rate),
               "-d", str(max(1, round(seconds))), str(target)]
    try:
        _ = subprocess.run(cmd, check=True, timeout=seconds + 5, capture_output=True)
        with wave.open(str(target), "rb") as captured:
            valid = captured.getnchannels() == 1 and captured.getsampwidth() == 2 and captured.getnframes() > 0
        if not valid:
            raise RecorderUnavailable("recording did not produce mono 16-bit PCM audio")
    except (OSError, wave.Error, subprocess.SubprocessError) as exc:
        target.unlink(missing_ok=True)
        raise RecorderUnavailable(f"microphone recording failed: {exc}") from exc
    return target
