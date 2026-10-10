"""Speech recognisers behind one small interface.

- VoskRecognizer: offline, Mandarin and English models exist
  (vosk-model-small-cn, vosk-model-small-en-us). There is no official Vosk
  Cantonese model; Cantonese users fall back to the Mandarin model with
  Cantonese aliases in the destination list, or to the button-cycling menu.
  TODO: evaluate a cloud ASR with yue-HK if the group accepts sending audio
  off the device (needs network anyway for navigation).
- TextRecognizer: takes text directly (diagnostics page / tests).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Protocol


class Recognizer(Protocol):
    def transcribe(self, wav_path: str | Path) -> str: ...


class TextRecognizer:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def transcribe(self, wav_path: str | Path) -> str:
        return self.text


VOSK_MODELS = {"cmn": "vosk-cmn", "yue": "vosk-cmn", "en": "vosk-en"}


class VoskRecognizer:
    def __init__(self, model_dir: str | Path, lang: str, phrases: list[str] | None = None) -> None:
        from vosk import KaldiRecognizer, Model  # optional dependency

        path = Path(model_dir) / VOSK_MODELS[lang]
        if not path.is_dir():
            raise FileNotFoundError(f"Vosk model not found: {path}")
        self._model = Model(str(path))
        self._Kaldi = KaldiRecognizer
        # restricting the grammar to destination names improves accuracy a lot
        self._grammar = json.dumps(phrases + ["[unk]"], ensure_ascii=False) if phrases else None

    def transcribe(self, wav_path: str | Path) -> str:
        with wave.open(str(wav_path), "rb") as wf:
            rec = (self._Kaldi(self._model, wf.getframerate(), self._grammar) if self._grammar
                   else self._Kaldi(self._model, wf.getframerate()))
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                rec.AcceptWaveform(data)
            return json.loads(rec.FinalResult()).get("text", "").replace(" ", "")
