"""Bounded Psyche gateway checks. No live requests without --live."""
import argparse
import io
import json
from pathlib import Path
import re
import time
import uuid
import wave

from .assistant_cli import read_jpeg
from .assistant_provider import AssistantError, PsycheGrokVisionProvider
from .psyche_budget import RequestBudget


def normalized_words(text):
    return re.findall(r"\w+", text.casefold())


def check_wav(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as sample:
            rate, channels, width, frames = (sample.getframerate(), sample.getnchannels(),
                                             sample.getsampwidth(), sample.getnframes())
            pcm = sample.readframes(frames)
            if (sample.getcomptype() != "NONE" or width != 2 or channels not in {1, 2}
                    or not 8000 <= rate <= 48000 or not 0 < frames <= 10 * rate
                    or len(pcm) != frames * channels * width):
                raise ValueError
    except (EOFError, ValueError, wave.Error):
        raise AssistantError("invalid_audio") from None
    return {"rate": rate, "channels": channels, "duration_s": round(frames / rate, 3)}


def includes_observation(answer, expected):
    text, fragment = " ".join(normalized_words(answer)), " ".join(normalized_words(expected))
    return bool(fragment and f" {fragment} " in f" {text} ")


def execute(args, provider):
    if args.capability == "models":
        return {"models": provider.models()}
    if args.capability == "text":
        reply = provider.describe("Reply with exactly CANE TEST to confirm text completion.", None, "en")
        if normalized_words(reply) != ["cane", "test"]:
            raise AssistantError("text_not_verified")
        return {"answer": reply, "request": provider.last_result}
    if args.capability == "vision":
        observations = []
        inputs = ((args.image, args.expect), (args.compare_image, args.compare_expect))
        images = [read_jpeg(path) for path, _ in inputs]
        first, second = (expected for _, expected in inputs)
        if (images[0] == images[1] or includes_observation(first, second)
                or includes_observation(second, first)):
            raise AssistantError("choose_distinct_vision_controls")
        for index, image in enumerate(images):
            expected, other = inputs[index][1], inputs[1-index][1]
            reply = provider.describe(args.question, image, "en")
            observations.append({"answer": reply, "request": provider.last_result,
                                 "matches": includes_observation(reply, expected)
                                 and not includes_observation(reply, other)})
        if not all(item["matches"] for item in observations):
            return {"verified": False, "comparison": observations}
        return {"comparison": observations}
    if not args.audio_endpoints_verified:
        raise AssistantError("audio_endpoints_not_documented")
    if args.capability == "asr":
        if args.wav.stat().st_size > 1024 * 1024:
            raise AssistantError("invalid_audio")
        data = args.wav.read_bytes()
        check_wav(data)
        credentials = provider.check(model_variable="PSYCHE_ASR_MODEL")
        result = provider._request("/audio/transcriptions", credentials=credentials,
                                   form={"model": credentials[1], "language": args.language},
                                   files={"file": ("sample.wav", data, "audio/wav")})
        transcript = result.get("text") if isinstance(result, dict) else None
        if not isinstance(transcript, str) or not transcript.strip() or credentials[0] in transcript:
            raise AssistantError("invalid_reply")
        return {"model": credentials[1], "transcript": transcript,
                "verified": normalized_words(transcript) == normalized_words(args.expect)}
    credentials = provider.check(model_variable="PSYCHE_TTS_MODEL")
    data = provider._request("/audio/speech", credentials=credentials, binary=True, payload={
        "model": credentials[1], "input": args.text, "voice": args.voice, "response_format": "wav",
    })
    info = check_wav(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("xb") as output:
        output.write(data)
    return {"model": credentials[1], "wav": info, "saved": str(args.out), "playback_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run-id", default="pi-acceptance")
    parser.add_argument("--budget-db", type=Path, default=Path("logs/psyche-requests.sqlite"))
    parser.add_argument("--daily-limit", type=int, default=50)
    parser.add_argument("--request-limit", type=int, choices=range(1, 26), default=25)
    parser.add_argument("--audio-endpoints-verified", action="store_true")
    modes = parser.add_subparsers(dest="capability", required=True)
    modes.add_parser("models")
    modes.add_parser("text")
    vision = modes.add_parser("vision")
    vision.add_argument("image", type=Path)
    vision.add_argument("compare_image", type=Path)
    vision.add_argument("--question", required=True)
    vision.add_argument("--expect", required=True)
    vision.add_argument("--compare-expect", required=True)
    asr = modes.add_parser("asr")
    asr.add_argument("wav", type=Path)
    asr.add_argument("--expect", required=True)
    asr.add_argument("--language", choices=("en", "zh"), default="en")
    tts = modes.add_parser("tts")
    tts.add_argument("--voice", required=True)
    tts.add_argument("--text", default="A chair is on your left.")
    tts.add_argument("--out", type=Path, default=Path("captures/psyche-probe.wav"))
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required before contacting the gateway")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", args.run_id):
        parser.error("Choose a simple run ID and keep it for the complete test run")
    if args.capability == "tts" and (len(args.text) > 260 or args.out.exists()):
        parser.error("Choose a short prompt and a new output file")
    budget = RequestBudget(args.budget_db, args.daily_limit, run_id=args.run_id, run_limit=args.request_limit)
    provider = PsycheGrokVisionProvider(timeout=12, budget=budget)
    record = {"request_id": uuid.uuid4().hex, "capability": args.capability}
    started = time.monotonic()
    try:
        record.update(execute(args, provider))
        record.setdefault("verified", True)
    except AssistantError as error:
        record.update(verified=False, error=str(error))
    except (OSError, ValueError):
        record.update(verified=False, error="invalid_test_input")
    record["latency_s"] = round(time.monotonic() - started, 3)
    print(json.dumps(record, ensure_ascii=False))
    return 0 if record["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
