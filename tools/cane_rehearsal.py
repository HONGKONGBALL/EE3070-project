#!/usr/bin/env python3
"""Rehearse the cane's K2/K1/K3/K4 flows on a computer, with simulated keys and a simulated location.

What is real: the cane's own navigation controller, destination dialogue, audio arbiter, Pico line
parser, phone HTTPS endpoint, and AMap (with the real key). The location comes from
cane_demo_location.py, exactly as it will on the Pi.

What is replaced: the serial port (key presses are injected as the JSON lines the Pico sends), the
microphone and recognizer (each utterance is given as text), the speaker (what would be spoken is
printed), the camera, and Grok unless PSYCHE_API_KEY is set (a small rule-based stand-in answers
with the same JSON actions).

    AMAP_KEY=... python tools/cane_rehearsal.py

Needs the packages the Pi runs with (fastapi, uvicorn, httpx, numpy, pyserial).

With --speech-models the voice wake-up ("嘿，阿帕次") is rehearsed too, with the real Silero VAD and
SenseVoice models: each sentence is synthesized by macOS `say` and played into the microphone
fixture. That needs Python with sherpa-onnx, and macOS.

It cannot show that the microphone, the AirPods, the real keys or Grok's response time work.
Those need the Pi.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import wave
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
RESULTS = []


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    log(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return None


class PicoFixture:
    """The serial port. It sends what the Pico firmware sends: range frames and key events."""

    def __init__(self):
        self.pending, self.writes, self.ms = [], [], 0
        self.closed = threading.Event()

    def readline(self):
        if self.closed.wait(0.05):
            return b""
        if self.pending:
            return self.pending.pop(0)
        self.ms += 50
        return json.dumps({"t": "d", "f": 390, "u": -1, "g": -1, "gs": 0, "ms": self.ms}).encode()

    def press(self, key, kind="short"):
        log(f"KEY {key} ({kind})")
        self.pending.append(json.dumps({"t": "b", "id": key, "ev": kind}).encode())

    def write(self, data):
        self.writes.append(data)

    def close(self):
        self.closed.set()


class Sink:
    """The speaker. It records what would be played and stays busy for a moment, like real playback."""

    def __init__(self, record, texts, speed):
        self.record, self.texts, self.speed = record, texts, speed
        self.enabled, self.state, self.error, self.last_played, self.target = True, "ready", "", None, "rehearsal"
        self.catalog, self.playback_id = None, None
        self.heard, self.current, self.ends = [], None, 0.0

    def play(self, clips, text=None, *, context=None, pcm=None):
        names = [Path(str(clip)).stem for clip in clips]
        if pcm:
            spoken = self.texts.pop(0) if self.texts else "(cue)"
            seconds = len(pcm[1]) / 2 / pcm[0]
        else:
            spoken = " + ".join(PROMPTS.get(name, name) for name in names)
            seconds = 0.5 + 0.12 * len(spoken)
        self.heard.append((time.monotonic(), "assistant" if pcm else names[0], spoken))
        log(f"   SPEAKER {'[助手]' if pcm else '[' + names[0] + ']'} {spoken}")
        self.current, self.ends = dict(context or {}), time.monotonic() + seconds / self.speed
        self.record("audio_start", **self.current)

    def busy(self):
        if self.current is not None and time.monotonic() >= self.ends:
            done, self.current = self.current, None
            self.record("audio_done", **done)
        return self.current is not None

    def stop(self):
        if self.current is not None:
            done, self.current = self.current, None
            self.record("audio_interrupted", **done)


class Voice:
    """Recognizer and synthesizer. The next recognised sentence is whatever the rehearsal sets."""

    def __init__(self, texts):
        self.next_utterance, self.texts = "", texts

    def transcribe(self, pcm):
        return self.next_utterance

    def synthesize(self, text):
        self.texts.append(text)
        seconds = min(8.0, 0.3 + 0.14 * len(text))
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as stream:
                stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                stream.writeframes(b"\x00\x08\x00\xf8" * int(seconds * 8000))
            return buffer.getvalue()


class Microphone:
    """A loud steady signal during a key turn, as before. Between turns: silence, or a queued recording."""

    LOUD, QUIET = b"\x00\x10\x00\xf0" * 800, bytes(3200)

    def __init__(self):
        self.recording, self.assistant = bytearray(), None

    def play(self, pcm):
        self.recording.extend(bytes(9600) + pcm + bytes(32000 * 2))

    @contextmanager
    def __call__(self):
        def read(timeout):
            time.sleep(0.02)
            if self.recording:
                block = bytes(self.recording[:3200])
                del self.recording[:3200]
                return block.ljust(3200, b"\x00")
            passive = self.assistant is not None and self.assistant.state == "PASSIVE_LISTENING"
            return self.QUIET if passive else self.LOUD

        yield read


def synthesized(text):
    """16 kHz mono PCM of a sentence, spoken by a Mandarin system voice."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "say.wav"
        subprocess.run(["say", "-v", "Tingting", "-o", str(path), "--data-format=LEI16@16000", text], check=True)
        with wave.open(str(path)) as stream:
            return stream.readframes(stream.getnframes())


class StandInGrok:
    """Answers with the JSON actions Grok gave on the Pi for the same kind of sentence."""

    def __init__(self):
        self.fail, self.calls = None, 0

    def chat(self, messages, *, max_tokens=200, request_id=None):
        from cane.vision.assistant_provider import AssistantError

        self.calls += 1
        if self.fail:
            raise AssistantError(self.fail)
        text = re.sub(r"\s+", "", messages[-1]["content"])
        state = messages[-2]["content"]
        listed = "候选地点：" in state
        reply = {"action": "ask", "query": "", "choice": 0, "reply": "您想去哪里？"}
        if "与目的地无关" in state and re.search(r"我的|手机|钥匙|杯子|什么颜色", text):
            reply = {"action": "other"}
        elif any(word in text for word in ("不去了", "取消", "算了", "停止")) and "想去" not in text:
            reply = {"action": "cancel", "query": "", "choice": 0, "reply": ""}
        elif any(word in text for word in ("出发", "好的", "确认", "可以")) and "想去" not in text:
            reply = {"action": "confirm", "query": "", "choice": 0, "reply": ""}
        elif listed and "最近" in text and "想去" not in text:
            reply = {"action": "choose", "query": "", "choice": 1, "reply": ""}
        elif listed and re.search(r"第([四五六七八九十]|[4-9])", text):
            reply = {"action": "ask", "query": "", "choice": 0, "reply": "没有这个编号，请选一到三。"}
        else:
            place = re.sub(r"^(不去了)?(我想去|我要去|带我去|帮我找一下|帮我找个?|去)", "", text)
            place = re.sub(r"^(附近的?|最近的?|一下)+", "", place)
            place = re.sub(r"(有没有|在哪里?|怎么走|吗|呢)", "", place)
            if place and place not in ("你好",):
                reply = {"action": "search", "query": place, "choice": 0, "reply": ""}
        return json.dumps(reply, ensure_ascii=False)

    def describe(self, question, jpeg, language, *, request_id=None, prompt=None):
        if prompt and "NAVIGATE" in prompt and re.search(r"星巴克|麦当劳|便利店|超市", question):
            return "NAVIGATE"
        return "画面里是一条走廊，左边有一扇门。"


def lan_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.168.255.255", 9))
        return probe.getsockname()[0]


def build(args, work):
    repo = Path(args.repo).resolve()
    sys.path[:0] = [str(repo / "pi"), str(repo / "tools")]
    os.chdir(work)
    (work / "config").mkdir()
    (work / "logs").mkdir()
    language = getattr(args, "language", "cmn")  # of the turn prompts: cmn or en
    (work / "audio" / "navigation" / language).mkdir(parents=True)
    nav_toml = (repo / "config" / "config.example.toml").read_text("utf-8")
    nav_toml = nav_toml.replace('dir = "audio"', 'dir = "audio/navigation"').replace('role = "ground"', 'role = "front"')
    nav_toml = re.sub(r"(\[imu\]\nenabled = )true", r"\1false", nav_toml)
    nav_toml = re.sub(r"(\[vision\]\nenabled = )true", r"\1false", nav_toml)
    nav_toml = nav_toml.replace('provider = "amap"', f'provider = "{getattr(args, "provider", "amap")}"')
    (work / "config" / "config.toml").write_text(nav_toml, "utf-8")
    address = args.address or lan_address()
    subprocess.run([sys.executable, str(repo / "tools" / "setup_pi_phone.py"), "--address", address,
                    "--directory", "certs/pi-phone"], check=True, stdout=subprocess.DEVNULL,
                   env=dict(os.environ, PYTHONPATH=str(repo / "pi")))
    (work / "config" / "camera.unified.toml").write_text(
        '[navigation]\nenabled = true\nconfig = "config/config.toml"\nphone_pair = "certs/pi-phone"\n'
        f'language = "{language}"\n', "utf-8")

    from unittest.mock import patch

    import cane.vision.wake_assistant as wake_assistant
    from cane.prompts import CATALOG
    from cane.vision.distance import PicoDistanceProvider
    from cane.vision.navigation import PiNavigation
    from cane.vision.speech import Speech
    from cane.vision.wake_assistant import WakeAssistant

    PROMPTS.update({name: prompt.text(language) for name, prompt in CATALOG.items()})
    pico, texts = PicoFixture(), []
    patch("cane.pico_link.SerialPicoLink._open", lambda _self: pico).start()
    patch.object(wake_assistant, "shared_jpeg", lambda camera: (b"jpeg", 1, time.monotonic())).start()
    audio = Speech(work / "audio" / "en", True, "", None, haptic=lambda pattern: None, info_preempts_navigation=True)
    sink = audio.sink = Sink(audio._record_audio, texts, args.speech_speed)
    distance = PicoDistanceProvider("rehearsal-serial")
    navigation = PiNavigation({"enabled": True, "config": "config/config.toml", "phone_pair": "certs/pi-phone",
                               "language": language, "demo_assume_stationary": args.imu == "stationary"}, distance, audio)
    voice = Voice(texts)
    if os.environ.get("PSYCHE_API_KEY"):
        from cane.vision.assistant_provider import PsycheGrokVisionProvider
        from cane.vision.psyche_budget import RequestBudget

        grok = PsycheGrokVisionProvider(15, budget=RequestBudget("logs/requests.sqlite", None))
        log("Grok: the real gateway")
    else:
        grok = StandInGrok()
        log("Grok: rule-based stand-in (no PSYCHE_API_KEY on this computer)")
    microphone, detector = Microphone(), None
    if args.speech_models:
        # The real wake-up path: Silero VAD cuts out each utterance and SenseVoice transcribes it.
        from cane.vision.sherpa_speech import SenseVoiceRecognizer, SpokenWake
        from cane.vision.wake_word import spoken_wake

        models = Path(args.speech_models).resolve()
        recognizer = SenseVoiceRecognizer(models, "zh", 2)
        recognizer.recognizer
        detector = SpokenWake(models / "silero_vad.onnx", recognizer, spoken_wake)
        log("voice wake-up: real Silero VAD and SenseVoice, sentences synthesized by `say`")
    assistant = WakeAssistant({"enabled": True, "mode": "psyche_grok", "question_language": "zh",
                               "answer_language": "zh", "max_record_s": 15, "request_timeout_s": 15,
                               "max_scene_age_s": 12, "wake_enabled": detector is not None}, object(), audio,
                              microphone=microphone, speech=voice, provider=grok, navigation=navigation,
                              detector=detector)
    microphone.assistant = assistant
    navigation.attach_cloud_assistant(assistant)
    # The same routing the camera service does for assistant speech.
    audio.on_delivery = lambda event_id, action, now: (
        assistant.delivery(event_id, action, now) if event_id.startswith("assistant:") else None)
    audio.validator = lambda request: (
        assistant.valid(request.event_id, time.monotonic()) if request.event_id.startswith("assistant:") else True)

    class Status(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"assistant": assistant.status()}, ensure_ascii=False).encode()
            self.send_response(200 if self.path == "/assistant/status" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    status = ThreadingHTTPServer(("127.0.0.1", 8000), Status)
    threading.Thread(target=status.serve_forever, daemon=True).start()
    stop = threading.Event()

    def tick():
        while not stop.wait(0.05):
            audio.tick()

    distance.start()
    navigation.start()
    assistant.start()
    threading.Thread(target=tick, daemon=True).start()
    return SimpleNamespace(pico=pico, sink=sink, voice=voice, grok=grok, assistant=assistant, navigation=navigation,
                           cane=navigation.controller, audio=audio, stop=stop, address=address, repo=repo,
                           microphone=microphone, wake=detector)


PROMPTS = {}


class Rehearsal:
    def __init__(self, world):
        self.w = world
        self.keys_over_http = False

    def key(self, name, kind="short"):
        """A key press: as a line from the Pico, or through the diagnostics endpoint that needs no Pico."""
        if not self.keys_over_http:
            return self.w.pico.press(name, kind)
        import urllib.request

        log(f"KEY {name} ({kind}) via /api/button")
        request = urllib.request.Request("http://127.0.0.1:8080/api/button", method="POST",
                                         data=json.dumps({"button": name, "kind": kind}).encode(),
                                         headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=4) as response:
            assert json.load(response)["accepted"]

    def idle(self, seconds=40):
        w = self.w
        return wait_for(lambda: w.assistant.status()["state"] == "PASSIVE_LISTENING" and w.assistant.request_id is None
                        and not w.audio.busy() and not w.audio.arbiter.pending(), seconds)

    def heard_since(self, mark):
        return [entry[2] for entry in self.w.sink.heard[mark:]]

    def say(self, text, key="dest"):
        """One K2 (or K4) turn: press, speak, press again. Returns what the speaker played afterwards."""
        self.idle(15)  # a turn that overlaps a spoken prompt is discarded, so wait for a quiet moment
        w, mark = self.w, len(self.w.sink.heard)
        log(f"USER says: {text}")
        w.voice.next_utterance = text
        self.key(key)
        if not wait_for(lambda: w.assistant.status()["state"] == "LISTENING_FOR_COMMAND", 8):
            return ["(recording did not start)"]
        time.sleep(0.4)
        self.key(key)
        wait_for(lambda: w.assistant.status()["state"] != "LISTENING_FOR_COMMAND", 5)
        self.idle()
        return self.heard_since(mark)

    def speak(self, sentence, expect_turn=True):
        """Say a sentence near the cane without touching a key. Returns what the speaker played afterwards."""
        self.idle(15)
        w, mark = self.w, len(self.w.sink.heard)
        log(f"USER says aloud: {sentence}")
        w.microphone.play(synthesized(sentence))
        started = bool(wait_for(lambda: w.assistant.status()["state"] != "PASSIVE_LISTENING", 12))
        wait_for(lambda: not w.microphone.recording, 12)
        if started != expect_turn:
            return [f"(turn started: {started}; the cane heard 「{w.wake.heard}」)"]
        self.idle()
        return self.heard_since(mark) + [f"heard 「{w.assistant.status().get('heard', '')}」"] if started else []

    def press(self, key, kind="short", settle=1.5):
        mark = len(self.w.sink.heard)
        self.key(key, kind)
        time.sleep(settle)
        self.idle(20)
        return self.heard_since(mark)


def scenarios(world, args):
    r, w = Rehearsal(world), world
    dialogue = lambda: w.assistant.status().get("destination_dialogue") or {}
    nav = lambda: w.navigation.status()

    check("Pico frames and the phone location reach the controller",
          wait_for(lambda: nav()["pico_ok"] and nav()["location_valid"], 25), str(nav()))

    if w.wake is not None:
        wake_scenarios(r, w, dialogue)

    log("--- 1. K2: nearest metro station -> candidates ---")
    heard = r.say("我想去最近的地铁站")
    names = [c["name"] for c in dialogue().get("candidates", [])]
    check("K2 search reads out candidates", len(names) >= 2 and any("找到" in h for h in heard), f"{names} | {heard[-1:]}")

    log("--- 2. K2: an ordinal that was not offered ---")
    heard = r.say("第四个")
    check("an unknown ordinal is refused, nothing is staged", dialogue().get("chosen") is None and bool(heard), str(heard[-1:]))

    log("--- 3. K1 before a destination is chosen ---")
    heard = r.press("ok")
    check("K1 without a destination does not start navigation", not nav()["active"] and not nav()["route_pending"], str(heard))

    log("--- 4. K2: choose the first one (handled locally, no cloud) ---")
    calls = getattr(w.grok, "calls", None)
    heard = r.say("第一个")
    check("the choice is staged and K1 is requested", dialogue().get("chosen") == (names[0] if names else None)
          and any("K1" in h for h in heard), str(heard[-1:]))
    if calls is not None:
        check("an ordinal does not use the cloud", w.grok.calls == calls)

    log("--- 5. K2: cancel, then search something else ---")
    heard = r.say("取消")
    check("cancel clears the choice", dialogue().get("chosen") is None, str(heard[-1:]))
    heard = r.say("附近有没有便利店")
    shops = [c["name"] for c in dialogue().get("candidates", [])]
    check("a second search works", len(shops) >= 1, str(shops))

    log("--- 6. cloud failure -> spoken notice and a plain map search ---")
    if hasattr(w.grok, "fail"):
        w.grok.fail = "provider_timeout"
        heard = r.say("我想去地铁站")
        w.grok.fail = None
        check("a cloud failure falls back to a local search", any("云端助手暂时不可用" in h for h in heard)
              and len(dialogue().get("candidates", [])) >= 1, str(heard[-1:]))

    log("--- 7. K2 metro station again, choose, K1 -> route and prompts while the location walks ---")
    r.say("我想去最近的地铁站")
    heard = r.say("第一个")
    target = dialogue().get("chosen")
    mark = len(w.sink.heard)
    w.pico.press("ok")
    started = wait_for(lambda: nav()["active"], 25)
    check("K1 starts navigation", started, f"to {target} | {nav()}")
    if started:
        time.sleep(3)
        if args.imu == "stationary":
            log("--- 7b. K1 while standing still: confirm the heading so that turn prompts are allowed ---")
            w.pico.press("ok")
            check("K1 confirms the heading", wait_for(lambda: w.cane.sm.aligned, 6), f"state {w.cane.sm.state}")
        time.sleep(4)
        log("--- 8. K3: repeat the current instruction ---")
        before = len(w.sink.heard)
        w.pico.press("repeat")
        check("K3 repeats an instruction", wait_for(lambda: len(w.sink.heard) > before, 8), str(r.heard_since(before)))
        log("--- 9. K4 (fourth key) during navigation: vision question ---")
        heard = r.say("前面有什么", key="mode")
        check("K4 answers a vision question and navigation continues", any("走廊" in h for h in heard) and nav()["active"],
              str(heard[-1:]))
        log(f"--- 10. walking to the destination (up to {args.walk_timeout} s) ---")
        deadline, crossings = time.monotonic() + args.walk_timeout, 0
        while nav()["active"] and time.monotonic() < deadline:
            if nav()["waiting_crossing"]:
                crossings += 1
                log("   the cane waits at a crossing; pressing K1 to confirm")
                w.pico.press("ok")
                time.sleep(3)
            time.sleep(0.5)
        prompts = [entry[1] for entry in w.sink.heard[mark:] if entry[1] != "assistant"]
        housekeeping = {"nav_start", "nav_stop_align", "nav_nothing_to_repeat", "nav_arrived", "crossing_confirmed"}
        turns = [name for name in prompts if name not in housekeeping]
        check("turn prompts were spoken on the way", len(turns) >= 2,
              f"{len(turns)} turn prompts {sorted(set(turns))}; 请停下对正 x{prompts.count('nav_stop_align')}")
        check("the cane announces arrival and navigation ends", not nav()["active"] and "nav_arrived" in prompts,
              f"crossings confirmed: {crossings}")

    log("--- 11. a second route with every key sent through /api/button (no Pico), stopped with a long K2 ---")
    time.sleep(10)  # the simulated location returns to the start point
    r.keys_over_http = hasattr(w.cane, "press_button")
    r.say("我想去最近的地铁站")
    r.say("第一个")
    r.key("ok")
    if wait_for(lambda: nav()["active"], 25):
        time.sleep(3)
        heard = r.press("dest", "long", settle=3)
        check("a long K2 stops navigation" + (" (keys injected without the Pico)" if r.keys_over_http else ""),
              not nav()["active"], str(heard[-1:]))
    else:
        check("a second route starts", False, str(nav()))
    r.keys_over_http = False

    log("--- 12. the location stops arriving ---")
    r.say("我想去最近的地铁站")
    r.say("第一个")
    w.pico.press("ok")
    if wait_for(lambda: nav()["active"], 25):
        world.feeder.terminate()
        mark = len(w.sink.heard)
        gone = wait_for(lambda: not nav()["active"], 30)
        check("navigation stops by itself when the phone location expires", gone, str(r.heard_since(mark)[-2:]))
        heard = r.say("我想去最近的地铁站")
        check("a search without a location says so", any("定位" in h for h in heard), str(heard[-1:]))


def wake_scenarios(r, w, dialogue):
    """Hey APT: one spoken sentence does what K2 or K4 does, and ordinary speech does nothing."""
    log("--- wake 1. ordinary speech near the cane is ignored ---")
    heard = r.speak("我想去最近的地铁站", expect_turn=False)
    check("speech without the wake phrase starts nothing", heard == [] and not dialogue().get("candidates"),
          f"{heard} | the cane heard 「{w.wake.heard}」")

    log("--- wake 2. a place in one breath -> the K2 dialogue ---")
    heard = r.speak("嘿，阿帕次，我想去最近的地铁站")
    names = [c["name"] for c in dialogue().get("candidates", [])]
    check("the wake phrase with a place reads out candidates", len(names) >= 2 and any("找到" in h for h in heard),
          f"{names} | {heard[-2:]}")

    log("--- wake 3. a question in one breath -> the K4 answer ---")
    heard = r.speak("嘿，阿帕次，前面有什么")
    check("the wake phrase with a question is answered from the camera, the candidates stay",
          any("走廊" in h for h in heard) and len(dialogue().get("candidates", [])) == len(names), str(heard[-2:]))

    log("--- wake 4. a question that sounds like a place -> Grok passes it to the camera ---")
    heard = r.speak("嘿，阿帕次，我的手机在哪")
    check("a doubtful request is passed on to the camera answer", any("走廊" in h for h in heard)
          and len(dialogue().get("candidates", [])) == len(names), str(heard[-2:]))

    log("--- wake 5. a spoken choice ---")
    heard = r.speak("嘿，阿帕次，第一个")
    check("the wake phrase with an ordinal stages that place and asks for K1",
          dialogue().get("chosen") == (names[0] if names else None) and any("K1" in h for h in heard), str(heard[-2:]))

    log("--- wake 6. only the wake phrase: cue, then the request ---")
    w.voice.next_utterance = "取消"
    mark = len(w.sink.heard)
    w.microphone.play(synthesized("嘿，阿帕次"))
    listening = wait_for(lambda: w.assistant.status()["state"] == "LISTENING_FOR_COMMAND", 12)
    time.sleep(0.4)
    r.key("dest")  # on the Pi the end of speech ends the recording; here a key does
    r.idle()
    check("the wake phrase alone plays the cue, records, and the request is carried out",
          listening and dialogue().get("chosen") is None and any("取消" in h for h in r.heard_since(mark)),
          str(r.heard_since(mark)[-2:]))


def phone_check(world, args):
    """Start the iPhone app over the cable with a pairing link for this computer, and wait for its location."""
    import hashlib
    import ssl

    pair_dir = Path("certs/pi-phone")
    pair = json.loads((pair_dir / "pair.json").read_text("utf-8"))
    pem = (pair_dir / "server.crt").read_text("utf-8")
    leaf = pem[pem.index("-----BEGIN CERTIFICATE-----"):pem.index("-----END CERTIFICATE-----") + 25]
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(leaf)).hexdigest()
    link = (f"ee3070-cane://mac-location?host={pair['address']}&port=8443"
            f"&fingerprint={fingerprint}&token={pair['token']}")
    result = subprocess.run(["xcrun", "devicectl", "device", "process", "launch", "--device", args.phone,
                             "--terminate-existing", "--payload-url", link, "hk.ee3070.NavigationCompanion"],
                            capture_output=True, text=True, timeout=90)
    tail = ((result.stderr or "") + (result.stdout or "")).strip().splitlines()
    check("the iPhone app starts with a pairing link for this computer", result.returncode == 0,
          "" if result.returncode == 0 else " | ".join(tail[-2:])[:220])
    if result.returncode != 0:
        return
    nav = world.navigation.status
    arrived = wait_for(lambda: nav()["location_valid"], 75)
    location = world.cane.location
    check("the app shares its real location without a tap", arrived,
          f"accuracy about {location[2]:.0f} m" if location else "no fix arrived in 75 s")
    if not arrived:
        return
    first = location[3]
    time.sleep(12)
    check("fixes keep arriving", world.cane.location[3] > first, f"{world.cane.location[3] - first:.1f} s newer")
    heard = Rehearsal(world).say("我想去最近的地铁站")
    names = [c["name"] for c in (world.assistant.status().get("destination_dialogue") or {}).get("candidates", [])]
    check("a K2 search from the phone's real position returns places", len(names) >= 1, f"{names} | {heard[-1:]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=str(HERE.parent), help="checkout of the cane code to rehearse")
    parser.add_argument("--address", help="this computer's LAN address for the phone endpoint (default: detected)")
    parser.add_argument("--walk-speed", type=float, default=6.0, help="simulated walking speed in m/s")
    parser.add_argument("--walk-timeout", type=int, default=240)
    parser.add_argument("--speech-speed", type=float, default=4.0, help="how much faster than real time the speaker plays")
    parser.add_argument("--imu", choices=("stationary", "none"), default="stationary",
                        help="stationary: an IMU that reports the cane is still, so K1 can confirm the heading")
    parser.add_argument("--keep", action="store_true", help="keep running after the scenarios, for the iPhone app")
    parser.add_argument("--phone", metavar="UDID", help="instead of the scenarios: check the iPhone app against this computer")
    parser.add_argument("--speech-models", metavar="DIR",
                        help="SenseVoice folder (model.int8.onnx, tokens.txt, silero_vad.onnx): also rehearse voice wake-up")
    args = parser.parse_args()
    if not os.environ.get("AMAP_KEY"):
        parser.error("AMAP_KEY is not set")
    with tempfile.TemporaryDirectory(prefix="cane-rehearsal-") as directory:
        work = Path(directory).resolve()
        world = build(args, work)
        log(f"cane services are up: phone endpoint https://{world.address}:8443, work dir {work}")
        feeder = [sys.executable, str(HERE / "cane_demo_location.py"), "--release", str(work), "--speed", str(args.walk_speed)]
        # With --phone the real iPhone is the only source of location.
        world.feeder = subprocess.Popen(["sleep", "100000"] if args.phone else feeder,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=lambda: [log("   LOCATION", line.rstrip()[9:]) for line in world.feeder.stdout], daemon=True).start()
        try:
            if args.phone:
                phone_check(world, args)
            else:
                scenarios(world, args)
            if args.keep:
                log("scenarios finished; services stay up (Ctrl-C to end)")
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            world.feeder.terminate()
            world.stop.set()
            passed = sum(1 for _, ok, _ in RESULTS if ok)
            log(f"=== {passed} of {len(RESULTS)} checks passed ===")
            for name, ok, detail in RESULTS:
                if not ok:
                    log("FAILED:", name, "|", detail)
            os._exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
