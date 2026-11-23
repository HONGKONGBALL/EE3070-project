"""Local speech recognition (Vosk) and synthesis (Piper) for the camera assistant, in English or Mandarin."""

from functools import cached_property
import io
import json
import re
import wave

from .voice_audio import ASR_RATE


# Model folder and voice file under speech_models_dir for each language.
MODELS = {
    "en": ("vosk-model-small-en-us-0.15", "en_US-lessac-low.onnx"),
    "zh": ("vosk-model-small-cn-0.22", "zh_CN-huayan-medium.onnx"),
}


class LocalSpeech:
    def __init__(self, model_directory, *, question_language="en", answer_language="en",
                 vosk_model=None, piper_voice=None):
        if question_language not in MODELS or answer_language not in MODELS:
            raise ValueError("Assistant question_language and answer_language must be en or zh")
        self.models = model_directory
        self.language = question_language
        self.vosk_model = vosk_model or MODELS[question_language][0]
        self.piper_voice = piper_voice or MODELS[answer_language][1]

    @cached_property
    def recognizer_model(self):
        import vosk

        vosk.SetLogLevel(-1)
        return vosk.Model(str(self.models / self.vosk_model))

    @cached_property
    def voice(self):
        from onnxruntime import InferenceSession, SessionOptions
        from piper import PiperVoice
        from piper.config import PiperConfig

        path = self.models / self.piper_voice
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
        text = ' '.join(part for part in words if part)
        if self.language == 'zh':
            # The Mandarin model separates every word with a space.
            text = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', text)
        return text

    def synthesize(self, text):
        with io.BytesIO() as buffer:
            with wave.open(buffer, 'wb') as wav:
                self.voice.synthesize_wav(text, wav)
            return buffer.getvalue()
