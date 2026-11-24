"""The wake phrase Hey APT: streaming recognition of its Korean pronunciation, and matching it in a
Mandarin transcript."""

import json
import unicodedata


DISPLAY_PHRASE = "Hey APT"
MANDARIN_PHRASE = "嘿，阿帕次"

# The Mandarin assistant has no keyword model. It transcribes every utterance and looks for the
# phrase at the start. SenseVoice writes 嘿，阿帕次 with whichever characters sound closest (黑阿帕次,
# 嘿阿巴斯, 阿八兹, 阿伯子, K安帕) and sometimes drops a syllable, so each character is matched by
# its reading. The tables below come from about a hundred recordings of the phrase by its user.
_READINGS = {
    # 嘿 and what it is taken for. "hei" are the ones that are rarely anything else.
    "hey": "嘿黑嗨诶欸喂还孩海亥害骇哎唉哀埃挨矮艾碍爱",
    "hei": "嘿黑嗨诶欸喂",
    # 阿. "an" leaves out 啊, which also opens ordinary sentences.
    "a": "阿啊吖呀安暗",
    "an": "阿安暗",
    "pa": "帕怕趴啪爬琶葩",
    # 帕 heard without its puff of air or with another vowel: ba, bo, po, pao, bao, bu, fa, pai.
    "ba": "巴八吧把罢霸拔叭芭疤扒伯柏博波拨播薄脖勃泊婆坡破泼迫颇炮泡跑抛袍刨包宝保抱报饱爆堡不布步部补捕簿发法拍派排牌",
    "b": "巴八扒",
    # 帕 heard as another stop.
    "ta": "特他她它塔踏克可",
    # 次: ci, si, te, ce, se, zi.
    "ci": "次此刺词辞慈瓷磁雌赐疵茨斯思私司丝撕嘶死四肆寺嗣伺饲巳特册策侧测厕色涩瑟子字紫资姿滋咨兹淄孜籽滓渍",
    "chi": "吃痴持池迟尺赤翅斥齿耻士氏仕车",
    # Further readings 次 is taken for. They also start many requests, so they only count as the
    # last syllable when nothing follows.
    "ci?": "是事时十使市式试师失施湿诗识实石食史始世室视示适势释饰似逝誓侍拾矢驶柿"
           "之只知指至直值制质治志支纸止织职植执址智置致枝汁脂七期其奇起气器汽弃棋旗骑齐妻欺漆启企"
           "提题体替梯踢的得德这者折哲克可刻客课科自",
}
_LATIN = {
    "hey": {"hey", "hi", "hay", "hei", "hej", "hello", "helo", "halo"},
    "hei": {"hey", "hi", "hay", "hei", "hej", "hello", "helo", "halo"},
    "a": {"a", "ah", "r", "ar", "uh"},
    "pa": {"p", "pa", "pah", "par"},
    "ci": {"t", "ts", "s", "c", "te", "tz", "z"},
    "apt": {"apt", "apts", "apat", "apart", "appt", "apate", "apateu", "apatu", "apartment"},
}
_SEPARATORS = " \t，,。.！!？?、：:；;~～…—-"
# Said instead of 嘿 by some users. The phrase may follow these two syllables.
_GREETINGS = ("你好", "您好", "哈喽", "哈罗", "哈啰", "哈尔")


def _sounds(unit):
    table = _LATIN if unit.isascii() else _READINGS
    return {name for name, members in table.items() if unit in members}


def _wake_at(sounds, start):
    """How many units from the start of the transcript the wake phrase takes when it begins at
    unit start, or 0. Each unit is the set of sounds it can stand for."""
    def has(index, name):
        return index < len(sounds) and name in sounds[index]

    def last(end):
        """1 when the unit at end is the last syllable of the phrase."""
        return int(has(end, "ci") or has(end, "chi") or (has(end, "ci?") and len(sounds) == end + 1))

    hey, hei = has(start, "hey"), has(start, "hei")
    at = start + 1 if hey else start
    if has(at, "apt"):
        return at + 1
    a, strong = has(at, "a"), has(at, "an")
    if a and has(at + 1, "pa"):
        # 阿帕 alone is too little after a bare 啊: it also needs 嘿 in front or 次 behind.
        return at + 2 + last(at + 2) if hey or last(at + 2) or strong else 0
    if a and has(at + 1, "ba"):
        return at + 3 if last(at + 2) and (strong or hey) else 0
    if strong and has(at + 1, "ta"):
        return at + 3 if last(at + 2) else 0
    if hei and has(start + 1, "an") and (has(start + 2, "ci") or has(start + 2, "chi")):
        return start + 3  # 嘿阿兹: 帕 was swallowed
    if hei and (has(start + 1, "pa") or has(start + 1, "b")) and has(start + 2, "ci"):
        return start + 3  # 嘿怕次, 嘿巴兹: 阿 ran into 嘿
    return 0


def spoken_wake(text, *, mandarin_only=False):
    """What was said after the wake phrase at the start of a transcript.

    Returns "" when nothing follows the phrase and None when the transcript does not start with it.
    """
    text = unicodedata.normalize("NFKC", text).strip()
    sounds, ends = [], []
    position = 0
    while position < len(text) and len(sounds) < 8:
        end = position + 1
        if text[position].isascii() and text[position].isalpha():
            while end < len(text) and text[end].isascii() and text[end].isalpha():
                end += 1
        elif text[position] in _SEPARATORS:
            position = end
            continue
        sounds.append(_sounds(text[position:end].lower()))
        ends.append(end)
        position = end
    # One misheard syllable may come first (K安帕, 配阿帕兹), or a greeting of two.
    if mandarin_only:
        if len(sounds) < 3 or text[ends[0] - 1] not in _READINGS["hei"]:
            return None
        length = _wake_at(sounds, 0)
        if length < 3 or not ({"ci", "chi", "ci?"} & sounds[length - 1]):
            return None
        return text[ends[length - 1]:].lstrip(_SEPARATORS).strip()
    length = _wake_at(sounds, 0) or _wake_at(sounds, 1)
    if not length and "".join(char for char in text if char not in _SEPARATORS).startswith(_GREETINGS):
        length = _wake_at(sounds, 2)
    if not length:
        return None
    return text[ends[length - 1]:].lstrip(_SEPARATORS).strip()


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
