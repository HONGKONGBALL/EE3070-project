"""Mandarin speech recognition with SenseVoice and end-of-speech detection with Silero VAD.

Both run offline through sherpa-onnx. The assistant uses them when [assistant] asr_backend is
"sensevoice". Vosk stays the default, and switching back is a config change. With wake_enabled
the same two models also listen for the wake phrase between key presses.
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
        return self.transcribe_samples(_floats(pcm)) if pcm else ""

    def transcribe_samples(self, samples):
        """Recognise float samples in [-1, 1] at ASR_RATE."""
        stream = self.recognizer.create_stream()
        stream.accept_waveform(ASR_RATE, samples)
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


class SpokenWake:
    """Wake the cane by voice without a keyword model.

    Every utterance near the microphone is cut out by the VAD and transcribed. One that starts with
    the wake phrase wakes the cane, and whatever followed the phrase is the request: in the same
    breath, or after a short pause. Only the first seconds of a long utterance are transcribed
    until the phrase is found, so that other people talking nearby cost little.
    """

    HEAD_S = 3.0
    # The VAD starts an utterance a little late and the first syllable of the phrase is lost
    # without this much audio from before it. Measured on recordings of the phrase.
    LEAD_S, TAIL_S = 0.3, 0.15
    # After the phrase alone, a request that starts within this time belongs to it.
    FOLLOW_S = 0.8
    KEEP_S = 30

    def __init__(self, vad_model, recognizer, match, silence_s=0.7, max_s=12.0, *, command_recognizer=None):
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(vad_model)
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = silence_s
        config.silero_vad.min_speech_duration = 0.25
        config.silero_vad.max_speech_duration = max_s
        config.sample_rate = ASR_RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=self.KEEP_S)
        self.recognizer, self.match = recognizer, match
        self.command_recognizer = command_recognizer or recognizer
        self.heard = ""  # the start of the last utterance, for bench checks
        self.reset()

    def reset(self):
        self.vad.reset()
        self.audio = np.zeros(0, dtype=np.float32)  # what the VAD has been given since the reset
        self.dropped = 0                            # samples already removed from its front
        self.follow_until = None                    # set while waiting for a request after the phrase

    def feed(self, pcm):
        """Take one microphone block.

        Returns the request spoken after the wake phrase, "" when only the phrase was said, and
        None while nobody has said it.
        """
        samples = _floats(pcm)
        self.vad.accept_waveform(samples)
        self.audio = np.concatenate((self.audio, samples))
        excess = len(self.audio) - self.KEEP_S * ASR_RATE
        if excess > 0:
            self.audio, self.dropped = self.audio[excess:], self.dropped + excess
        request = None
        while not self.vad.empty():
            start, length = self.vad.front.start, len(self.vad.front.samples)
            self.vad.pop()
            if request is not None:
                continue
            utterance = self._cut(start, length)
            if self.follow_until is not None:
                # The phrase came alone a moment ago. This utterance is the request.
                self.follow_until = None
                request = self.command_recognizer.transcribe_samples(utterance)
                self.heard = request[:16]
                continue
            request = self._request(utterance)
            if request == "":
                # Only the phrase. Give the user a moment to go on before answering with the cue.
                self.follow_until = self.dropped + len(self.audio) + round(self.FOLLOW_S * ASR_RATE)
                request = None
        if request is None and self.follow_until is not None:
            if self.dropped + len(self.audio) >= self.follow_until and not self.vad.is_speech_detected():
                self.follow_until = None
                return ""
        return request

    def _cut(self, start, length):
        """The utterance with a little audio before and after what the VAD marked."""
        first = max(0, start - round(self.LEAD_S * ASR_RATE) - self.dropped)
        last = start + length + round(self.TAIL_S * ASR_RATE) - self.dropped
        return self.audio[first:max(first, last)]

    def _request(self, samples):
        head = samples[:round(self.HEAD_S * ASR_RATE)]
        text = self.recognizer.transcribe_samples(head)
        self.heard = text[:16]
        request = self.match(text)
        if request is None or len(head) == len(samples):
            return request
        # The phrase opens a longer utterance. Transcribe all of it for the request.
        full = self.match(self.recognizer.transcribe_samples(samples))
        return request if full is None else full
