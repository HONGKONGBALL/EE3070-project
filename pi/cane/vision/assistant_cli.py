"""Diagnostics for the mic, offline ASR, local TTS, and the Psyche API."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

from cane.voice.asr import VoskRecognizer

from .assistant_media import RATE, AlsaRecorder, EspeakSynthesizer
from .assistant_provider import AssistantError, OpenAICompatibleVisionProvider


def read_jpeg(path):
    import cv2
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise AssistantError("cannot_read_test_image")
    height, width = image.shape[:2]
    scale = min(1, 640 / max(height, width))
    if scale < 1:
        image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise AssistantError("cannot_encode_test_image")
    return encoded.tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("microphones")
    commands.add_parser("models")
    commands.add_parser("text-test")
    capture = commands.add_parser("record")
    capture.add_argument("output", type=Path)
    capture.add_argument("--device", default="default")
    capture.add_argument("--seconds", type=int, choices=range(1, 9), default=3)
    asr = commands.add_parser("transcribe")
    asr.add_argument("wav", type=Path)
    asr.add_argument("--models", type=Path, default=Path("models"))
    asr.add_argument("--language", choices=("en", "zh"), default="en")
    internal = commands.add_parser("decode-pcm", help=argparse.SUPPRESS)
    internal.add_argument("models", type=Path)
    internal.add_argument("language", choices=("en", "cmn"))
    tts = commands.add_parser("tts")
    tts.add_argument("text")
    tts.add_argument("--language", choices=("en", "zh"), default="en")
    tts.add_argument("--target", default="")
    vision = commands.add_parser("vision-test")
    vision.add_argument("image", type=Path)
    vision.add_argument("--question", default="What is the main object in this image?")
    vision.add_argument("--expect", required=True, help="A visible detail not given away by the question")
    args = parser.parse_args()

    if args.command == "decode-pcm":
        if hasattr(os, 'nice'):
            os.nice(10)
        pcm = sys.stdin.buffer.read(2 * RATE * 20)
        result = VoskRecognizer(args.models, args.language).transcribe_pcm(pcm, RATE)
        sys.stdout.buffer.write(result.encode('utf-8'))
    elif args.command == "microphones":
        subprocess.run(["arecord", "-l"], check=True)
        subprocess.run(["arecord", "-L"], check=True)
    elif args.command == "record":
        if args.output.exists():
            parser.error("Choose a new recording path")
        recording = AlsaRecorder(args.device, args.seconds).start()
        try:
            time.sleep(args.seconds)
        finally:
            pcm = recording.stop()
        if len(pcm) < 3200:
            raise AssistantError("empty_audio")
        with args.output.open('xb') as handle, wave.open(handle, 'wb') as wav:
            wav.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            wav.writeframes(pcm)
    elif args.command == "transcribe":
        with wave.open(str(args.wav), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, RATE):
                parser.error("Use a 16 kHz mono 16-bit PCM WAV")
            if wav.getnframes() > 20 * RATE:
                parser.error("Use a recording no longer than 20 seconds")
            pcm = wav.readframes(wav.getnframes())
        lang = 'cmn' if args.language == 'zh' else 'en'
        print(VoskRecognizer(args.models, lang).transcribe_pcm(pcm, RATE))
    elif args.command == "tts":
        rate, pcm = EspeakSynthesizer().synthesize(args.text[:260], args.language)
        command = ['pw-play'] + (['--target', args.target] if args.target else [])
        subprocess.run(command + ['--raw', '--rate', str(rate), '--channels', '1', '--format', 's16', '-'],
                       input=pcm, check=True, timeout=20)
    else:
        provider = OpenAICompatibleVisionProvider()
        start = time.monotonic()
        if args.command == "models":
            print('\n'.join(provider.models()))
            return
        provider.check()
        if args.command == "text-test":
            print(provider.describe("Reply with the word connected to confirm that chat works.", None, "en"))
        else:
            answer = provider.describe(args.question, read_jpeg(args.image), "en")
            print(answer)
            if args.expect.casefold() not in answer.casefold():
                raise AssistantError("vision_not_verified")
        print(f"Request completed in {time.monotonic() - start:.2f}s")


if __name__ == '__main__':
    try:
        main()
    except AssistantError as exc:
        raise SystemExit(str(exc)) from None
