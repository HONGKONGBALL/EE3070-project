"""Local demo acceptance for ground/crowd replay and push-to-talk visual answers."""

import argparse
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace
from unittest.mock import patch

import cv2
from gpiozero import Device
from gpiozero.pins.mock import MockFactory
from piper import SynthesisConfig

from cane.vision.speech import Speech
from cane.vision.v2 import V2, load_config
from cane.vision.wake_assistant import WakeAssistant
from check_pi_unified import CameraFixture, wait_for
from check_wake_local import FixtureMicrophone, TimedSink, TracedSpeech, synthesize_input, write_audio
from voice_audio import convert_pcm, read_wave
from voice_demo import LocalSpeech


HERE = Path(__file__).parent


def automatic(case, args):
    source = args.media_root / case["media"]
    config = load_config("config/camera.v2.toml", mode="awareness", models_root=args.models_root)
    with patch("cane.vision.demo.Camera", lambda *values: CameraFixture(
        source if case["kind"] == "image" else args.media_root / "Ollama/vlm-eval/images/H01.jpg", *values
    )):
        app = V2(config)
    microphone = FixtureMicrophone(())
    sink = app.audio.sink = TimedSink(app.audio._record_audio, microphone)
    sink.state, sink.error, sink.last_played, sink.target = "ready", "", None, ""
    app.start()
    events = {}
    saved = {}
    try:
        def loaded():
            active = [s for s in app.task_states.values() if s["state"] != "disabled"]
            if any(s["state"] == "error" for s in active):
                raise RuntimeError(str(app.task_states))
            return all(s["state"] == "ready" for s in active)

        wait_for(loaded, "vision models", seconds=18)
        if case["kind"] == "video":
            app.camera.video = source
        started = time.monotonic()
        while time.monotonic() - started < case.get("seconds", 7):
            status = app.status()
            for event in status["recent_events"]:
                if event["created_at"] < started and case["kind"] == "video":
                    continue
                events[event["event_id"]] = event["phrase_id"]
                phrase = event["phrase_id"]
                if phrase not in saved:
                    with app.condition:
                        jpeg, frame_id = app.jpeg, app.frame_id
                    if jpeg and frame_id >= (event.get("frame_id") or 0):
                        name = f"case-{case['id']:02d}-{phrase}.jpg"
                        (args.out / name).write_bytes(jpeg)
                        saved[phrase] = {"image": name, "event_frame": event["frame_id"], "preview_frame": frame_id}
            time.sleep(.15)
        status = app.status()
        with app.condition:
            jpeg = app.jpeg
        if not saved and jpeg:
            name = f"case-{case['id']:02d}-preview.jpg"
            (args.out / name).write_bytes(jpeg)
            saved["preview"] = {"image": name, "preview_frame": status["frame_id"]}
        started_audio = [phrase for phrase, _, _ in sink.entries]
        emitted = set(events.values())
        missed = bool(case.get("want") and emitted.isdisjoint(case["want"]))
        false_warnings = sorted(emitted.intersection(case.get("avoid", [])))
        errors = {name: state["error"] for name, state in status["tasks"].items() if state["state"] == "error"}
        missing_audio = bool(case.get("want") and not set(started_audio).intersection(case["want"]))
        return {"pass": not (missed or missing_audio or false_warnings or errors), "events": sorted(emitted),
                "audio": started_audio, "missed": missed, "unexpected": false_warnings, "errors": errors,
                "missing_audio": missing_audio, "evidence": saved,
                "preview_fps": status["fps"], "last_frame": status["frame_id"], "run_id": app.run_id}
    finally:
        app.close()


def visual(cases, args):
    input_voice = SynthesisConfig(noise_scale=0, noise_w_scale=0)
    speech = LocalSpeech(args.speech_models)
    trace = TracedSpeech(speech)
    mic = FixtureMicrophone(())
    sound = Speech(args.out, True, ordinary_interval_s=0)
    sink = sound.sink = TimedSink(sound._record_audio, mic)
    camera = SimpleNamespace(frame=None, frame_id=None)
    camera.next_frame = lambda *_a, **_k: (camera.frame, camera.frame_id, time.monotonic())
    Device.pin_factory = pins = MockFactory()
    voice = WakeAssistant({"ptt_gpio": 17, "wake_enabled": False, "request_timeout_s": 15,
                           "max_scene_age_s": 20, "speech_models_dir": args.speech_models},
                          camera, sound, microphone=mic.open, speech=trace)
    sound.validator = lambda req: not req.priority.is_assistant or voice.valid(req.event_id, time.monotonic())
    sound.on_delivery = voice.delivery
    results = {}
    voice.start()
    try:
        for case in cases:
            ident = case["id"]
            camera.frame = cv2.imread(str(args.media_root / case["media"]))
            camera.frame_id = ident
            if camera.frame is None:
                raise FileNotFoundError(case["media"])
            wav_source = args.recordings / f"{ident}.wav" if args.recordings else None
            if wav_source and wav_source.is_file():
                audio, rate, channels = read_wave(wav_source.read_bytes())
                pcm = convert_pcm(audio, rate, channels, 16000)
                source = "SAPI fixture"
            else:
                pcm = synthesize_input(speech, case["question"], input_voice)
                source = "Piper fixture"
            mic.utterances = iter((pcm,))
            before, played = len(trace.timeline), len(sink.entries)
            pin = pins.pin(17)
            pin.drive_low()
            wait_for(lambda: mic.active and mic.utterance_sent.is_set(), "input audio", seconds=4)
            assert len(trace.timeline) == before
            release = time.monotonic()
            pin.drive_high()
            first_audio = None
            while time.monotonic() - release < 24:
                sound.tick()
                if len(sink.entries) > played and first_audio is None:
                    first_audio = round(time.monotonic() - release, 3)
                state = voice.status()
                if state["state"] == "PASSIVE_LISTENING" and state["request_id"] is None:
                    break
                time.sleep(.015)
            else:
                raise TimeoutError(voice.status())
            turns = trace.timeline[before:]
            answer = next((line[1] for line in turns if line[0] == "tts"), "")
            transcription = next((line[1] for line in turns if line[0] == "asr"), "")
            audible = False
            speech_seconds = None
            if len(sink.entries) > played and sink.entries[-1][2]:
                rate, rendered = sink.entries[-1][2]
                speech_seconds = round(len(rendered) / (2 * rate), 3)
                write_audio(args.out / f"case-{ident:02d}.wav", rate, rendered)
                audible = bool(rendered)
            accurate = bool(re.search(case["match"], answer, re.I) and
                            not (case.get("avoid_text") and re.search(case["avoid_text"], answer, re.I)))
            result = {"pass": audible and accurate and not state["error"], "input": source,
                      "reference": case["reference"], "transcription": transcription, "answer": answer,
                      "first_audio_s": first_audio, "synthesis_s": next((x[2] for x in turns if x[0] == "tts"), None),
                      "transcription_s": next((x[2] for x in turns if x[0] == "asr"), None),
                      "speech_seconds": speech_seconds,
                      "utterance_words": len(answer.split()), "error": state["error"],
                      "frame_id": state["frame_id"]}
            results[ident] = result
            print(f"{ident}: {result['pass']} | {transcription} | {answer}", flush=True)
    finally:
        voice.close()
        sound.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--speech-models", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--recordings", type=Path)
    parser.add_argument("--only", default="all", choices=("all", "auto", "voice"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = json.loads((HERE / "pi_demo_cases.json").read_text(encoding="utf-8"))
    if args.only in {"all", "auto"}:
        results = {}
        for case in cases:
            if case["kind"] in {"image", "video"}:
                result = results[case["id"]] = automatic(case, args)
                print(f"{case['id']}: {result}", flush=True)
        (args.out / "automatic.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    if args.only in {"all", "voice"}:
        results = visual([c for c in cases if c["kind"] == "voice"], args)
        (args.out / "voice.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
