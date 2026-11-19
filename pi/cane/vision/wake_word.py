"""Streaming offline recognition of the Korean pronunciation of Hey APT."""

import json


DISPLAY_PHRASE = "Hey APT"
PRONUNCIATION = "헤이 아파트"
GRAMMAR = (PRONUNCIATION, "아파트", "헤이", "[3]")
# These are acoustic spellings for the second decoder, not accepted wake phrases by themselves.
PHONETIC = {"hey a pot", "hey a pot to", "hey a pot too", "hey a pa", "hey a pah",
            "hey a pah to", "hey a part", "hey a part to", "hey a part two"}
ENGLISH_GRAMMAR = sorted(PHONETIC | {"hey a p t", "a p t", "hey apartment", "hey up to you",
                                   "hey apple", "hey peter", "[unk]"})


class WakeDetector:
    def __init__(self, model_dir, english_model, minimum_confidence=0.70):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        self.decoder = KaldiRecognizer(Model(str(model_dir)), 16000, json.dumps(GRAMMAR, ensure_ascii=False))
        self.decoder.SetWords(True)
        self.minimum_confidence = minimum_confidence
        self.english_model = english_model
        self.pcm = bytearray()

    def reset(self):
        self.decoder.Reset()
        self.pcm.clear()

    def feed(self, pcm):
        self.pcm.extend(pcm)
        if len(self.pcm) > 96_000:
            del self.pcm[:-96_000]
        if not self.decoder.AcceptWaveform(pcm):
            return False
        utterance = json.loads(self.decoder.Result())
        audio = bytes(self.pcm)
        self.pcm.clear()
        return self.matches(utterance) and self.verify_phonetics(audio)

    def verify_phonetics(self, pcm):
        from vosk import KaldiRecognizer

        decoder = KaldiRecognizer(self.english_model(), 16000, json.dumps(ENGLISH_GRAMMAR))
        decoded = []
        if decoder.AcceptWaveform(pcm):
            decoded.append(json.loads(decoder.Result()).get("text", ""))
        decoded.append(json.loads(decoder.FinalResult()).get("text", ""))
        text = " ".join(part for part in decoded if part)
        return text.removesuffix(" [unk]") in PHONETIC

    def matches(self, utterance):
        words = utterance.get("result", [])
        return (utterance.get("text") == PRONUNCIATION
                and len(words) == 2
                and all(word["conf"] >= self.minimum_confidence for word in words))


def main():
    import argparse
    from pathlib import Path

    from .local_speech import LocalSpeech
    from .voice_audio import ASR_RATE, convert_pcm, read_wave

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--confidence", type=float, default=0.70)
    parser.add_argument("recordings", type=Path, nargs="+")
    args = parser.parse_args()
    speech = LocalSpeech(args.models)
    detector = WakeDetector(args.models / "vosk-model-small-ko-0.22", lambda: speech.recognizer_model,
                            args.confidence)
    results = []
    for path in args.recordings:
        samples, rate, channels = read_wave(path.read_bytes())
        pcm = convert_pcm(samples, rate, channels, ASR_RATE) + bytes(ASR_RATE * 2)
        detector.reset()
        detected_at = None
        for offset in range(0, len(pcm), 3200):
            if detector.feed(pcm[offset:offset + 3200]):
                detected_at = round(min(offset + 3200, len(pcm)) / (2 * ASR_RATE), 3)
                break
        results.append({"file": str(path), "detected_at_s": detected_at})
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
