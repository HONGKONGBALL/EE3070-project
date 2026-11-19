"""Local English speech recognition and synthesis for the camera assistant."""

from functools import cached_property
import io
import json
import wave

from .voice_audio import ASR_RATE


class LocalSpeech:
    def __init__(self, model_directory):
        self.models = model_directory

    @cached_property
    def recognizer_model(self):
        import vosk

        vosk.SetLogLevel(-1)
        return vosk.Model(str(self.models / 'vosk-model-small-en-us-0.15'))

    @cached_property
    def voice(self):
        from onnxruntime import InferenceSession, SessionOptions
        from piper import PiperVoice
        from piper.config import PiperConfig

        path = self.models / 'en_US-lessac-low.onnx'
        config = PiperConfig.from_dict(json.loads(path.with_suffix('.onnx.json').read_text('utf-8')))
        options = SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        session = InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
        return PiperVoice(config=config, session=session)

    def transcribe(self, pcm):
        from vosk import KaldiRecognizer

        recognizer = KaldiRecognizer(self.recognizer_model, ASR_RATE)
        words = []
        for offset in range(0, len(pcm), 8000):
            if recognizer.AcceptWaveform(pcm[offset:offset + 8000]):
                words.append(json.loads(recognizer.Result())['text'])
        words.append(json.loads(recognizer.FinalResult())['text'])
        return ' '.join(part for part in words if part)

    def synthesize(self, text):
        with io.BytesIO() as buffer:
            with wave.open(buffer, 'wb') as wav:
                self.voice.synthesize_wav(text, wav)
            return buffer.getvalue()
