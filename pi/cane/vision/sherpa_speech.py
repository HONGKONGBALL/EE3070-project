"""Mandarin speech recognition with SenseVoice and end-of-speech detection with Silero VAD.

Both run offline through sherpa-onnx. The assistant uses them when [assistant] asr_backend is
"sensevoice". Vosk stays the default, and switching back is a config change.
"""

from functools import cached_property
from pathlib import Path

import numpy as np

from .voice_audio import ASR_RATE


def _floats(pcm):
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


class SenseVoiceRecognizer:
    """Offline recognizer with the same transcribe(pcm) call as the Vosk one."""

    def __init__(self, directory, language="zh", threads=2):
        self.directory, self.language, self.threads = Path(directory), language, threads

    @cached_property
    def recognizer(self):
        import sherpa_onnx

        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(self.directory / "model.int8.onnx"), tokens=str(self.directory / "tokens.txt"),
            num_threads=self.threads, language=self.language, use_itn=True)

    def transcribe(self, pcm):
        if not pcm:
            return ""
        stream = self.recognizer.create_stream()
        stream.accept_waveform(ASR_RATE, _floats(pcm))
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()


class SpeechEndpoint:
    """Reports the end of an utterance: speech was heard and then stayed quiet for silence_s."""

    def __init__(self, model, silence_s=0.8, threshold=0.5):
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model)
        config.silero_vad.threshold = threshold
        config.silero_vad.min_silence_duration = silence_s
        config.silero_vad.min_speech_duration = 0.25
        config.silero_vad.max_speech_duration = 20
        config.sample_rate = ASR_RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        self.heard = False

    def reset(self):
        self.vad.reset()
        self.heard = False

    def feed(self, pcm):
        """Take one microphone block. True once a complete utterance has ended."""
        self.vad.accept_waveform(_floats(pcm))
        if self.vad.is_speech_detected():
            self.heard = True
        return not self.vad.empty()
