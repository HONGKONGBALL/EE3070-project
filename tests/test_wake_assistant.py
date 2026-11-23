from contextlib import contextmanager
import io
from threading import Event
import time
from types import SimpleNamespace

import pytest

from cane.arbiter import OutputArbiter
from cane.model import OutputRequest, Priority
from cane.vision.assistant_provider import AssistantError
from cane.vision.assistant_media import speech_only_pcm
from cane.vision.voice_commands import MockNavigationAdapter, confirmed, route_command
from cane.vision.wake_assistant import WakeAssistant, short_visual_answer
from cane.vision.wake_word import PRONUNCIATION, WakeDetector, spoken_wake


@pytest.mark.parametrize(("text", "expected"), [
    ("Navigate to CityU Library.", ("plan", "CityU Library")),
    ("navigate to city you library", ("plan", "CityU Library")),
    ("Take me to the library", ("plan", "the library")),
    ("导航去大学图书馆", ("plan", "大学图书馆")),
    ("What’s my next turn?", ("next", None)),
    ("My next turn", ("next", None)),
    ("Where am I?", ("status", None)),
    ("What's my navigation status?", ("status", None)),
    ("How far to my destination?", ("status", None)),
    ("Cancel navigation", ("stop", None)),
    ("下一步怎么走", ("next", None)),
    ("接下来往哪走", ("next", None)),
    ("离目的地还有多远", ("status", None)),
    ("还有多远啊", ("status", None)),
    ("还要走多久", ("status", None)),
    ("海岸城怎么走", ("vision", None)),
    ("椅子还有多远", ("vision", None)),
    ("Navigate to", ("unsupported_navigation", None)),
    ("What is in front of me?", ("vision", None)),
    ("Is the way ahead clear?", ("visual_safety", None)),
    ("Can I cross it?", ("visual_safety", None)),
    ("How far away is the chair?", ("vision", None)),
])
def test_voice_routes(text, expected):
    assert route_command(text) == expected


def test_pcm_keeps_speech_context_and_skips_constant_input():
    silence = b"\x00\x00" * 6400
    speech = b"\x00\x04\x00\xfc" * 320
    assert speech_only_pcm(b"\x00\x04" * 6400, 220) == b""
    assert speech_only_pcm(silence + speech + silence, 220) == silence[:5120] + speech + silence[:5120]


def test_spoken_visual_answer_keeps_first_complete_sentence():
    assert short_visual_answer("A chair is on your right. There is a light overhead.") == "A chair is on your right."


def test_route_requires_affirmation_and_mock_does_not_claim_success():
    assert confirmed("Yes, please!")
    assert confirmed("Yes.") and not confirmed("yes but wait")
    assert not MockNavigationAdapter().plan_route("CityU Library").success


@pytest.mark.parametrize(("text", "rest"), [
    ("黑阿帕次", ""), ("嘿，阿帕次。", ""), ("嘿阿帕特", ""), ("黑阿帕斯", ""), ("阿怕是", ""), ("嘿 A P T", ""),
    ("黑暗帕次我想去最近的地铁站", "我想去最近的地铁站"),
    ("黑阿怕次，前面有什么？", "前面有什么?"),
    ("阿帕次带我去麦当劳", "带我去麦当劳"),
    ("Hey APT 我想去图书馆", "我想去图书馆"),
    # SenseVoice dropped or misheard the first or the last syllable.
    ("K安帕我想去最近的地铁", "我想去最近的地铁"),
    ("安帕前面有什么", "前面有什么"),
    ("嗯，嘿阿帕次，第一个", "第一个"),
    ("黑怕次取消导航", "取消导航"),
    ("嘿阿帕可以走吗", "可以走吗"),
    ("嘿阿帕次可以走吗", "可以走吗"),
    # What SenseVoice wrote for the user's own recordings of the phrase.
    ("嘿阿帕兹", ""), ("阿帕斯", ""), ("配阿帕兹", ""), ("阿怕吃", ""), ("阿婆吃", ""), ("阿特兹", ""), ("阿克斯", ""),
    ("你好阿帕斯", ""), ("HELLO 阿柏兹", ""), ("喂阿帕吃", ""),
    ("阿巴斯带我去麦当劳", "带我去麦当劳"),
    ("嘿阿八兹第一个", "第一个"),
    ("嘿阿伯子前面可以走吗", "前面可以走吗"),
    ("嘿阿兹前面可以走吗", "前面可以走吗"),
    ("嘿巴兹前面有什么", "前面有什么"),
    ("嘿阿发兹取消导航", "取消导航"),
])
def test_wake_phrase_is_found_by_its_sound_at_the_start_of_a_transcript(text, rest):
    assert spoken_wake(text) == rest


@pytest.mark.parametrize("text", [
    "", "我想去最近的地铁站", "阿姨怕吃辣椒", "他怕次", "前面有什么东西", "啊怕是不行的", "啊怕什么", "第一个",
    "黑色的帕子", "各位老师同学大家好", "害怕死了", "还安排了什么", "我饿怕了", "和阿爸去超市",
    # Everyday openings that are close to the looser readings.
    "阿爸吃饭了吗", "阿姨您好", "啊不是吧", "啊不吃了", "嘿不是吧", "啊法子", "饿不死", "嘿包子好吃吗", "你好阿姨",
    "啊他吃了吗", "喂爸爸是我", "还不是因为你", "你好我想问一下", "啊八点了", "哈喽大家好",
])
def test_ordinary_speech_is_not_the_wake_phrase(text):
    assert spoken_wake(text) is None


def test_wake_grammar_means_korean_pronunciation():
    detector = object.__new__(WakeDetector)
    detector.minimum_confidence = 0.7
    assert detector.matches({"text": PRONUNCIATION, "result": [{"conf": 0.9}, {"conf": 0.8}]})
    assert not detector.matches({"text": "헤이 에이피티", "result": [{"conf": 1}, {"conf": 1}]})
    assert not detector.matches({"text": PRONUNCIATION + " [unk]", "result": [{"conf": 1}] * 3})
    assert not detector.matches({"text": PRONUNCIATION, "result": [{"conf": 1}, {"conf": 0.5}]})


class Sink:
    def __init__(self):
        self.played = []
        self.active = False

    def play(self, ids, text=None):
        self.played.append(ids[0])
        self.active = True

    def busy(self):
        return self.active

    def stop(self):
        self.active = False


@pytest.mark.parametrize("priority", [Priority.CRITICAL, Priority.SAFETY, Priority.INFO, Priority.ASSISTANT_NAV])
def test_higher_priority_interrupts_scene_answer_without_resuming(priority):
    sink = Sink()
    arbiter = OutputArbiter(sink)
    assert arbiter.submit(OutputRequest("answer", Priority.ASSISTANT))
    assert arbiter.submit(OutputRequest("higher_priority", priority))
    assert sink.played == ["answer", "higher_priority"]
    sink.stop()
    arbiter.tick()
    assert sink.played == ["answer", "higher_priority"]


def test_an_already_queued_ordinary_warning_holds_an_assistant_answer():
    sink = Sink()
    clock = [100.]
    arbiter = OutputArbiter(sink, clock=lambda: clock[0], ordinary_interval_s=1.5)
    arbiter.submit(OutputRequest("previous", Priority.INFO))
    sink.stop()
    arbiter.submit(OutputRequest("ordinary", Priority.SAFETY))
    arbiter.submit(OutputRequest("answer", Priority.ASSISTANT))
    assert sink.played == ["previous"]
    clock[0] += 1.5
    arbiter.tick()
    assert sink.played == ["previous", "ordinary"]


def test_manual_trigger_keeps_one_request_and_requires_opt_in_for_test_wake():
    assistant = WakeAssistant({"wake_enabled": False}, None, None, microphone=object(), speech=object(),
                              provider=SimpleNamespace())
    with pytest.raises(AssistantError, match="test_disabled"):
        assistant.test_wake()
    token = assistant.press()
    with pytest.raises(AssistantError, match="busy"):
        assistant.press()
    assert not assistant.release("another token")
    assert assistant.release(token, cancelled=True)
    assert not assistant.release(token)


def test_disconnected_microphone_leaves_status_available():
    disconnected = Event()

    @contextmanager
    def missing_input():
        disconnected.set()
        raise AssistantError("microphone_unavailable")
        yield

    assistant = WakeAssistant({}, None, None, microphone=missing_input, speech=object(), provider=object())
    assistant.start()
    try:
        assistant.press()
        assert disconnected.wait(1)
        assistant.worker.join(timeout=0.05)
        assert assistant.status()["error"] == "microphone_unavailable"
    finally:
        assistant.close()


def test_ptt_idle_hold_empty_and_bounce_do_not_request_ollama(monkeypatch):
    opening, recorded, idle = Event(), Event(), Event()
    transcribed = []
    requests = []
    spoken = []
    audio = SimpleNamespace(busy=lambda: False, sink=SimpleNamespace(enabled=True))

    @contextmanager
    def microphone():
        opening.set()

        def read(timeout):
            recorded.set()
            time.sleep(0.01)
            return b"\x00\x04\x00\xfc" * 320

        yield read

    assistant = WakeAssistant({"wake_enabled": False, "dashboard_wake_test": False}, None, audio,
                              microphone=microphone,
                              speech=SimpleNamespace(transcribe=lambda pcm: transcribed.append(pcm) or ""),
                              provider=SimpleNamespace(answer_question=lambda *args: requests.append(args)))
    original_state = assistant._state

    def state(name, error=""):
        original_state(name, error)
        if name == "PASSIVE_LISTENING":
            idle.set()

    monkeypatch.setattr(assistant, "_state", state)
    monkeypatch.setattr(assistant, "_speak", lambda text, **kwargs: spoken.append(text))
    assistant.start()
    try:
        assert not opening.wait(0.05)
        token = assistant.press()
        assert recorded.wait(1)
        assert not transcribed and not requests
        with pytest.raises(AssistantError, match="busy"):
            assistant.press()
        assert assistant.release(token)
        assert not assistant.release(token)
        assert idle.wait(2)
        assert len(transcribed) == 1
        assert not requests and not spoken
    finally:
        assistant.close()


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("demo", [False, True])
def test_ptt_sends_one_shared_frame_and_drops_cancelled_model_response(monkeypatch, cancelled, demo):
    from PIL import Image

    frame = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(frame, format="JPEG")
    snapshots, requests, spoken, pauses = [], [], [], []

    @contextmanager
    def exclusive_vision():
        pauses.append("pause")
        try:
            yield
        finally:
            pauses.append("resume")

    def snapshot(camera):
        snapshots.append(camera)
        return frame.getvalue(), 41, time.monotonic()

    def infer(question, image):
        requests.append((question, image.size))
        assert pauses == (["pause"] if demo else [])
        with pytest.raises(AssistantError, match="busy"):
            assistant.press()
        if cancelled:
            assert not assistant.release("other request", cancelled=True)
            assert assistant.release(token, cancelled=True)
            assert not assistant.release(token, cancelled=True)
        return SimpleNamespace(success=True, answer="A chair. Another sentence.")

    audio = SimpleNamespace(sink=SimpleNamespace(enabled=True))
    assistant = WakeAssistant({}, object(), audio, microphone=object(), provider=SimpleNamespace(answer_question=infer),
                              exclusive_vision=exclusive_vision if demo else None)
    monkeypatch.setattr("cane.vision.wake_assistant.shared_jpeg", snapshot)
    monkeypatch.setattr(assistant, "_transcribe", lambda pcm: "what is in front of me")
    monkeypatch.setattr(assistant, "_speak", lambda text, **kwargs: spoken.append(text) or True)
    token = assistant.press()
    assert assistant.release(token)
    assistant._turn("ptt", b"")
    assert snapshots == [assistant.camera]
    assert requests == [("What is in front of me?", (16, 16))]
    expected = ["Stand still. Camera alerts will pause."] if demo else []
    assert spoken == expected + ([] if cancelled else ["A chair."])
    assert pauses == (["pause", "resume"] if demo else [])
    assert assistant.answer == ("" if cancelled else "A chair.")


def test_ptt_navigation_reuses_adapter_without_calling_ollama(monkeypatch):
    navigation_requests = []
    nav = MockNavigationAdapter()
    monkeypatch.setattr(
        nav, "plan_route", lambda destination: navigation_requests.append(destination) or SimpleNamespace(message="Route planned."))
    monkeypatch.setattr(
        nav, "get_next_instruction", lambda: navigation_requests.append("next") or SimpleNamespace(message="Turn right."))

    @contextmanager
    def microphone():
        yield object()

    assistant = WakeAssistant({}, None, SimpleNamespace(sink=SimpleNamespace(enabled=True)), microphone=microphone,
                              navigation=nav, provider=object())
    transcripts = iter(["Navigate to CityU Library", "Yes", "What's my next turn?"])
    monkeypatch.setattr(assistant, "_transcribe", lambda pcm: next(transcripts))
    monkeypatch.setattr(assistant, "_capture", lambda mic: b"")
    spoken = []
    monkeypatch.setattr(assistant, "_speak", lambda text, **kwargs: spoken.append(text) or True)
    assistant._turn("ptt", b"")
    assistant._turn("ptt", b"")
    assert navigation_requests == ["CityU Library", "next"]
    assert spoken == ["Do you want to navigate to CityU Library?", "Route planned.", "Turn right."]
