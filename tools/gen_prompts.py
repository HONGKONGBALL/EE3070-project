#!/usr/bin/env python3
"""Pre-generate every prompt clip in Mandarin, Cantonese and English.

    python tools/gen_prompts.py --lang all --out audio/ [--backend google|espeak]

google: Google Cloud Text-to-Speech (cmn-CN, yue-HK, en-US). Credentials from
        GOOGLE_APPLICATION_CREDENTIALS - never commit the key file.
espeak: espeak-ng, offline, robotic; fine for bench testing.

Preset destination names from the config are generated as dest_<id>.
Run tools/check_lead_time.py afterwards: the longest language decides the
warning distance, and long sentences should be shortened.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi"))

from cane.config import LANGUAGES, Destination, load_config  # noqa: E402
from cane.prompts import CATALOG, destination_prompt_id  # noqa: E402

GOOGLE_LANG = {"cmn": "cmn-CN", "yue": "yue-HK", "en": "en-US"}
ESPEAK_VOICE = {"cmn": "cmn", "yue": "yue", "en": "en-us"}


def destination_text(d: Destination, lang: str) -> str:
    if lang == "en":
        for a in d.aliases:
            if a.isascii():
                return a.title()
    return d.name


def prompt_texts(lang: str, destinations: list[Destination]) -> dict[str, str]:
    texts = {pid: p.text(lang) for pid, p in CATALOG.items()}
    for d in destinations:
        texts[destination_prompt_id(d.id)] = destination_text(d, lang)
    return texts


def synth_google(text: str, lang: str, out: Path, rate: float = 1.1) -> None:
    from google.cloud import texttospeech as tts

    client = synth_google.client = getattr(synth_google, "client", None) or tts.TextToSpeechClient()
    resp = client.synthesize_speech(
        input=tts.SynthesisInput(text=text),
        voice=tts.VoiceSelectionParams(language_code=GOOGLE_LANG[lang]),
        audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.LINEAR16, sample_rate_hertz=22050,
                                     speaking_rate=rate),
    )
    out.write_bytes(resp.audio_content)  # LINEAR16 comes with a WAV header


def synth_espeak(text: str, lang: str, out: Path, rate: float = 1.1) -> None:
    subprocess.run(["espeak-ng", "-v", ESPEAK_VOICE[lang], "-s", str(int(170 * rate)), "-w", str(out), text],
                   check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="all", choices=["all", *LANGUAGES])
    ap.add_argument("--out", default=str(ROOT / "audio"))
    ap.add_argument("--backend", default="google", choices=["google", "espeak"])
    ap.add_argument("--config", default=str(ROOT / "config" / "config.example.toml"))
    ap.add_argument("--rate", type=float, default=1.1, help="speaking rate; faster = more lead time")
    ap.add_argument("--only", nargs="*", help="only these prompt ids")
    args = ap.parse_args(argv)

    cfg = load_config(args.config, env={})
    synth = synth_google if args.backend == "google" else synth_espeak
    langs = LANGUAGES if args.lang == "all" else (args.lang,)
    for lang in langs:
        out_dir = Path(args.out) / lang
        out_dir.mkdir(parents=True, exist_ok=True)
        for pid, text in prompt_texts(lang, cfg.nav.destinations).items():
            if args.only and pid not in args.only:
                continue
            synth(text, lang, out_dir / f"{pid}.wav", args.rate)
            print(f"{lang}/{pid}.wav  {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
