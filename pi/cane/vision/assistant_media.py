"""On-demand microphone capture, shared camera frames, and local speech."""

import io
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
import wave

from .assistant_provider import AssistantError


RATE = 16_000


class AlsaRecorder:
    """Read bounded PCM into memory while the button is held."""

    def __init__(self, device, max_seconds):
        self.device = device
        self.max_seconds = max_seconds

    def start(self):
        return AlsaRecording(self.device, self.max_seconds)


class AlsaRecording:
    def __init__(self, device, max_seconds):
        try:
            self.process = subprocess.Popen(
                ["arecord", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", str(RATE),
                 "-c", "1", "-d", str(math.ceil(max_seconds))],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise AssistantError("microphone_unavailable") from exc
        self.pcm = b""
        self.reader = threading.Thread(target=self._read, daemon=True, name="cane-microphone")
        self.reader.start()

    def _read(self):
        self.pcm, _ = self.process.communicate()

    def stop(self):
        already_failed = self.process.poll() not in (None, 0)
        if self.process.poll() is None:
            self.process.terminate()
        self.reader.join(timeout=1)
        if self.reader.is_alive():
            self.process.kill()
            self.reader.join(timeout=1)
        if self.reader.is_alive() or (already_failed and not self.pcm):
            raise AssistantError("microphone_unavailable")
        return self.pcm


class MockRecorder:
    def start(self):
        return self

    def stop(self):
        return b"\x01\x00" * (RATE // 2)


class MockRecognizer:
    def __init__(self, language):
        self.language = language

    def transcribe(self, pcm):
        return "我前面有什么？" if self.language == "zh" else "What is in front of me?"


class OfflineRecognizer:
    def __init__(self, model_dir, language, timeout):
        self.model_dir = Path(model_dir).resolve()
        self.language = "cmn" if language == "zh" else "en"
        self.timeout = timeout

    def transcribe(self, pcm):
        # A bounded child keeps Vosk CPU work and model loading away from the vision workers.
        try:
            result = subprocess.run(
                [sys.executable, "-m", "cane.vision.assistant_cli", "decode-pcm", str(self.model_dir), self.language],
                input=pcm, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AssistantError("asr_timeout") from exc
        except OSError as exc:
            raise AssistantError("asr_unavailable") from exc
        if result.returncode:
            raise AssistantError("asr_unavailable")
        return result.stdout.decode("utf-8").strip()


class EspeakSynthesizer:
    def synthesize(self, text, language):
        try:
            result = subprocess.run(["espeak-ng", "--stdout", "--stdin", "-v", "cmn" if language == "zh" else "en"],
                                    input=text.encode("utf-8"), capture_output=True, timeout=4, check=True)
            with wave.open(io.BytesIO(result.stdout), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                    raise AssistantError("tts_unavailable")
                rate = wav.getframerate()
                pcm = wav.readframes(rate * 16)
            if not pcm:
                raise AssistantError("tts_unavailable")
            return rate, pcm
        except (OSError, EOFError, wave.Error, subprocess.SubprocessError) as exc:
            raise AssistantError("tts_unavailable") from exc


def shared_jpeg(camera):
    import cv2
    from .quality import assess_frame

    frame = camera.next_frame(0, timeout=0)
    if frame is None:
        raise AssistantError("camera_unavailable")
    image, frame_id, captured_at = frame
    if time.monotonic() - captured_at > 1.5:
        raise AssistantError("camera_unavailable")
    if not assess_frame(image)["usable"]:
        raise AssistantError("camera_quality")
    height, width = image.shape[:2]
    scale = min(1, 640 / max(height, width))
    if scale < 1:
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise AssistantError("camera_quality")
    return jpeg.tobytes(), frame_id, captured_at
