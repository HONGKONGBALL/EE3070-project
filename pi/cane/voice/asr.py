"""Speech recognisers behind one small interface.

- VoskRecognizer: optional local Mandarin and English models. Cantonese input
  is explicitly unavailable; Cantonese output and text input remain separate.
- TextRecognizer: takes text directly (diagnostics page / tests).
"""

from __future__ import annotations

import json
import re
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Protocol


class ASRUnavailable(RuntimeError):
    """Offline ASR cannot run because its dependency or model is missing."""


class ASRInputError(ValueError):
    """The recording is not compatible with the offline speech model."""


class Recognizer(Protocol):
    def transcribe(self, wav_path: str | Path) -> str: ...


class TextRecognizer:
    def __init__(self, text: str = "") -> None:
        self.text: str = text

    def transcribe(self, wav_path: str | Path) -> str:
        return self.text


VOSK_MODELS: Final = {"cmn": "vosk-cmn", "en": "vosk-en"}
_HAN: Final = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_CHUNKS: Final = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[^\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\s]+")


class VoskVocabulary(Protocol):
    def vosk_model_find_word(self, word: str) -> int: ...


def _segment_han(text: str, vocabulary: VoskVocabulary) -> list[str] | None:
    """Find a complete lexicon path, preferring fewer tokens over greedy OOV."""
    paths: list[list[str] | None] = [None] * (len(text) + 1)
    paths[-1] = []
    for start in range(len(text) - 1, -1, -1):
        for end in range(len(text), start, -1):
            suffix = paths[end]
            token = text[start:end]
            if suffix is None or vocabulary.vosk_model_find_word(token) < 0:
                continue
            candidate = [token, *suffix]
            current = paths[start]
            if current is None or len(candidate) < len(current):
                paths[start] = candidate
    return paths[0]


def _model_phrase(phrase: str, vocabulary: VoskVocabulary) -> str | None:
    """Preserve English word boundaries and segment only contiguous Han text."""
    tokens: list[str] = []
    for chunk in _CHUNKS.findall(phrase.casefold()):
        if _HAN.fullmatch(chunk):
            segmented = _segment_han(chunk, vocabulary)
            if segmented is None:
                return None
            tokens.extend(segmented)
        elif vocabulary.vosk_model_find_word(chunk) >= 0:
            tokens.append(chunk)
        else:
            return None
    return " ".join(tokens) if tokens else None


def compile_grammar(phrases: Sequence[str] | None, lang: str,
                    vocabulary: VoskVocabulary) -> str | None:
    """Compile supported names, or use open recognition if any name is OOV.

    Dropping an unsupported destination would bias speech toward a different
    requested name. Open recognition lets the destination parser reject it.
    Control words are optional hints and are included only when supported.
    """
    if not phrases:
        return None
    supported: list[str] = []
    for phrase in phrases:
        compiled = _model_phrase(phrase, vocabulary)
        if compiled is None:
            return None
        if compiled not in supported:
            supported.append(compiled)
    controls = ("取消", "确认", "重试") if lang == "cmn" else ("cancel", "confirm", "retry")
    for control in controls:
        compiled = _model_phrase(control, vocabulary)
        if compiled is not None and compiled not in supported:
            supported.append(compiled)
    return json.dumps([*supported, "[unk]"], ensure_ascii=False)


class VoskRecognizer:
    def __init__(self, model_dir: str | Path, lang: str, phrases: list[str] | None = None) -> None:
        model_name = VOSK_MODELS.get(lang)
        if model_name is None:
            raise ASRUnavailable(f"unsupported offline ASR language: {lang}")
        path = Path(model_dir) / model_name
        if not path.is_dir():
            raise ASRUnavailable(f"Vosk model not found: {path}")
        try:
            from vosk import KaldiRecognizer, Model  # optional dependency
        except ImportError as exc:
            raise ASRUnavailable("Vosk dependency is not installed") from exc
        self._model = Model(str(path))
        self._Kaldi = KaldiRecognizer
        self._grammar = compile_grammar(phrases, lang, self._model)

    def transcribe(self, wav_path: str | Path) -> str:
        with wave.open(str(wav_path), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getcomptype() != "NONE":
                raise ASRInputError("Vosk requires uncompressed mono 16-bit PCM WAV")
            return self._transcribe(iter(lambda: wf.readframes(4000), b""), wf.getframerate())

    def transcribe_pcm(self, pcm: bytes, rate: int) -> str:
        return self._transcribe((pcm[i:i + 8000] for i in range(0, len(pcm), 8000)), rate)

    def _transcribe(self, chunks, rate):
        rec = (self._Kaldi(self._model, rate, self._grammar) if self._grammar
               else self._Kaldi(self._model, rate))
        segments: list[str] = []
        for chunk in chunks:
            if rec.AcceptWaveform(chunk):
                self._append_text(segments, rec.Result())
        self._append_text(segments, rec.FinalResult())
        return " ".join(segments)

    @staticmethod
    def _append_text(segments: list[str], payload: str) -> None:
        match json.loads(payload):
            case {"text": str(text)} if text.strip():
                segments.append(text.strip())
            case _:
                return
