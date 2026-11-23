"""Cloud (psyche_grok) assistant: K4/K2 taps, the wake phrase, spoken failures, ACK retry and request budget."""
from contextlib import contextmanager
import threading
import time
from types import SimpleNamespace

import pytest

from cane.vision import wake_assistant
from cane.vision.assistant_provider import AssistantError, PsycheGrokVisionProvider
from cane.vision.navigation import measured_amap_fetch
from cane.vision.psyche_budget import RequestBudget
from cane.vision.wake_assistant import CLOUD_FAILURE_SPEECH, SERVICE_UNAVAILABLE, WakeAssistant


class SaidOnce:
    """A wake detector that hears the wake phrase once, followed by the given request."""

    def __init__(self, request):
        self.pending = [request]

    def reset(self):
        pass

    def feed(self, block):
        return self.pending.pop() if self.pending else None


class Harness:
    """A WakeAssistant in cloud mode with fake audio, microphone, speech and provider."""

    def __init__(self, monkeypatch, describe=None, *, rejected_cues=0, transcript="what is in front of me",
                 navigation=None, config=None, chat=None, detector=None):
        self.cues, self.spoken, self.questions = [], [], []
        self.rejected_cues = rejected_cues
        self.idle = threading.Event()
        self.recording = threading.Event()
        audio = SimpleNamespace(busy=lambda: False, sink=SimpleNamespace(enabled=True),
                                submit_assistant=self.submit, cancel_assistant=lambda event_id: None)

        @contextmanager
        def microphone():
            def read(timeout):
                self.recording.set()
                time.sleep(0.01)
                return b"\x00\x10\x00\xf0" * 320
            yield read

        def default_describe(question, jpeg, language, *, request_id=None):
            self.questions.append(question)
            return "A chair is ahead. A lamp is behind it."

        speech = SimpleNamespace(transcribe=lambda pcm: transcript)
        self.assistant = WakeAssistant({"mode": "psyche_grok", "wake_enabled": detector is not None,
                                        **(config or {})}, object(), audio, detector=detector,
                                       microphone=microphone, speech=speech, navigation=navigation,
                                       provider=SimpleNamespace(describe=describe or default_describe, chat=chat))
        monkeypatch.setattr(wake_assistant, "shared_jpeg", lambda camera: (b"jpeg", 7, time.monotonic()))
        monkeypatch.setattr(wake_assistant, "ACK_RETRY_S", 0.02)
        monkeypatch.setattr(self.assistant, "_speak", lambda text, **kwargs: self.spoken.append(text) or True)
        original = self.assistant._state

        def state(name, error=""):
            original(name, error)
            if name == "PASSIVE_LISTENING":
                self.idle.set()
        monkeypatch.setattr(self.assistant, "_state", state)

    def submit(self, event_id, clip, captured_at, max_age, navigation=False):
        self.cues.append(event_id)
        if len(self.cues) <= self.rejected_cues:
            return False
        threading.Timer(0.01, self.assistant.delivery, (event_id, "completed", time.monotonic())).start()
        return True

    def ask(self, channel="vision_button"):
        assistant = self.assistant
        assert assistant.toggle(channel)
        assert self.recording.wait(2)
        time.sleep(0.3)  # Debounce window for the second tap
        self.idle.clear()
        assert assistant.toggle(channel)
        assert self.idle.wait(3)

    def wait_for_state(self, state, timeout=3):
        deadline = time.monotonic() + timeout
        while self.assistant.state != state and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self.assistant.state == state

    def __enter__(self):
        self.assistant.start()
        return self

    def __exit__(self, *exc):
        self.assistant.close()


def test_two_k4_taps_ask_one_cloud_question_and_speak_the_first_sentence(monkeypatch):
    with Harness(monkeypatch) as h:
        h.ask()
        assert h.assistant.state == "PASSIVE_LISTENING" and not h.assistant.error
    assert h.questions == ["what is in front of me"]
    assert h.spoken == ["A chair is ahead."]


@pytest.mark.parametrize(("error", "speech"), [
    ("provider_timeout", CLOUD_FAILURE_SPEECH["provider_timeout"]),
    ("budget_exhausted", CLOUD_FAILURE_SPEECH["budget_exhausted"]),
    ("provider_error", SERVICE_UNAVAILABLE),
    ("missing_key", SERVICE_UNAVAILABLE),
])
def test_cloud_failure_is_spoken_and_kept_on_the_dashboard(monkeypatch, error, speech):
    def fail(question, jpeg, language, *, request_id=None):
        raise AssistantError(error)

    with Harness(monkeypatch, fail) as h:
        h.ask()
        assert h.assistant.error == error
    assert h.spoken == [speech]


def test_unheard_question_is_reported_instead_of_silence(monkeypatch):
    with Harness(monkeypatch, transcript="") as h:
        h.ask()
    assert h.spoken == ["I didn't catch that. Please tap and ask again."]


def test_ack_waits_for_a_warning_to_finish_before_recording(monkeypatch):
    with Harness(monkeypatch, rejected_cues=3) as h:
        h.ask()
    assert len(h.cues) == 4
    assert h.spoken == ["A chair is ahead."]


def test_ack_that_never_plays_does_not_start_recording(monkeypatch):
    monkeypatch.setattr(wake_assistant, "ACK_WAIT_S", 0.1)
    with Harness(monkeypatch, rejected_cues=10_000) as h:
        h.idle.clear()
        assert h.assistant.toggle("vision_button")
        assert h.idle.wait(3)
        assert not h.recording.is_set()
        assert not h.questions and not h.spoken
        # A later tap starts a fresh request because the user never heard the cue.
        assert h.assistant.trigger is None


def test_k2_tap_sends_the_transcript_to_navigation_without_cloud_vision(monkeypatch):
    destinations = []
    navigation = SimpleNamespace(submit_destination=destinations.append)
    with Harness(monkeypatch, transcript="CityU Library", navigation=navigation) as h:
        h.ask("navigation_button")
    assert destinations == ["CityU Library"]
    assert not h.questions and not h.spoken


def test_k2_long_press_cancels_a_destination_recording(monkeypatch):
    destinations = []
    navigation = SimpleNamespace(submit_destination=destinations.append)
    with Harness(monkeypatch, navigation=navigation) as h:
        assert h.assistant.toggle("navigation_button")
        assert h.recording.wait(2)
        h.idle.clear()
        h.assistant.cancel_navigation_recording()
        assert h.idle.wait(3)
    assert not destinations and not h.spoken


def test_question_in_one_breath_with_the_wake_phrase_is_answered_like_k4(monkeypatch):
    with Harness(monkeypatch, detector=SaidOnce("what is in front of me")) as h:
        assert h.idle.wait(3)
        assert not h.assistant.error
    assert h.questions == ["what is in front of me"]
    assert h.spoken == ["A chair is ahead."]
    assert len(h.cues) == 1  # the short tone, no "start speaking" cue


def test_wake_phrase_alone_plays_the_cue_and_records_the_request(monkeypatch):
    with Harness(monkeypatch, detector=SaidOnce("")) as h:
        h.wait_for_state("LISTENING_FOR_COMMAND")
        assert h.assistant.in_turn()
        time.sleep(0.3)
        assert h.assistant.toggle("navigation_button")  # any key ends the recording early
        assert h.idle.wait(3)
    assert h.questions == ["what is in front of me"]
    assert h.spoken == ["A chair is ahead."]


def test_wake_phrase_followed_by_silence_asks_to_say_it_again(monkeypatch):
    with Harness(monkeypatch, transcript="", detector=SaidOnce("")) as h:
        h.wait_for_state("LISTENING_FOR_COMMAND")
        time.sleep(0.3)
        assert h.assistant.toggle("vision_button")
        assert h.idle.wait(3)
    assert h.spoken == ["I didn't catch that. Please say it again."]


def test_keys_still_work_while_the_cane_listens_for_the_wake_phrase(monkeypatch):
    with Harness(monkeypatch, detector=SaidOnce(None)) as h:
        assert h.recording.wait(2)  # passive listening holds the microphone
        h.ask()
    assert h.questions == ["what is in front of me"]
    assert h.spoken == ["A chair is ahead."]


def test_missing_microphone_while_listening_for_the_wake_phrase_stays_silent(monkeypatch):
    opened = threading.Event()

    @contextmanager
    def missing():
        opened.set()
        raise AssistantError("microphone_unavailable")
        yield

    h = Harness(monkeypatch, detector=SaidOnce("hello"))
    h.assistant.microphone = missing
    with h:
        assert opened.wait(2)
        time.sleep(0.2)
        assert h.assistant.status()["error"] == "microphone_unavailable"
        assert h.assistant.state == "PASSIVE_LISTENING"
    assert not h.spoken and not h.cues


def test_broken_wake_detector_turns_itself_off_and_leaves_the_keys(monkeypatch):
    class Broken:
        def reset(self):
            pass

        def feed(self, block):
            raise RuntimeError("model missing")

    with Harness(monkeypatch, detector=Broken()) as h:
        deadline = time.monotonic() + 2
        while h.assistant.wake_enabled and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not h.assistant.wake_enabled
        h.ask()
    assert h.spoken == ["A chair is ahead."]


class Route:
    """A navigation that reports where the active route stands."""

    def __init__(self, progress):
        self.report = progress

    def progress(self):
        return self.report


ZH = {"answer_language": "zh", "question_language": "zh", "destination_dialogue": False}


@pytest.mark.parametrize(("progress", "answer"), [
    ({"located": True, "remaining_m": 1234.0, "to_turn_m": 37.0, "maneuver": "turn_left", "crossing": False,
      "turns_ok": True}, "前方约35米左转，距离目的地还有约1.2公里。"),
    ({"located": True, "remaining_m": 240.0, "to_turn_m": 12.0, "maneuver": "turn_right", "crossing": False,
      "turns_ok": False}, "距离目的地还有约240米。请先站稳，按K1对正方向。"),
    ({"located": True, "remaining_m": 14.0, "to_turn_m": 14.0, "maneuver": None, "crossing": False,
      "turns_ok": True}, "距离目的地还有约15米。"),
    ({"located": True, "remaining_m": 300.0, "to_turn_m": 2.0, "maneuver": "turn_left", "crossing": True,
      "turns_ok": False}, "前方是路口。决定过街后按K1继续。"),
    ({"located": False}, "正在等待定位。"),
    (None, "现在没有在导航。"),
])
def test_how_far_is_answered_from_the_route_without_the_cloud(monkeypatch, progress, answer):
    with Harness(monkeypatch, transcript="还有多远", navigation=Route(progress), config=ZH) as h:
        h.ask()
    assert h.spoken == [answer]
    assert not h.questions


def test_next_turn_asked_after_the_wake_phrase_is_answered_from_the_route(monkeypatch):
    progress = {"located": True, "remaining_m": 510.0, "to_turn_m": 48.0, "maneuver": "slight_right",
                "crossing": False, "turns_ok": True}
    with Harness(monkeypatch, navigation=Route(progress), config=ZH, detector=SaidOnce("下一步怎么走")) as h:
        assert h.idle.wait(3)
    assert h.spoken == ["前方约50米向右前方走，距离目的地还有约510米。"]
    assert not h.questions


def test_wake_up_in_cloud_mode_needs_the_sensevoice_backend():
    assistant = WakeAssistant({"mode": "psyche_grok", "wake_enabled": True}, None, None, microphone=object(),
                              speech=object(), provider=object())
    assert not assistant.wake_enabled


def test_toggle_is_ignored_outside_cloud_mode():
    assistant = WakeAssistant({"mode": "ollama"}, None, None, microphone=object(), speech=object(), provider=object())
    assert not assistant.toggle("vision_button")


def test_budget_without_limits_never_touches_disk(tmp_path):
    budget = RequestBudget(tmp_path / "sub" / "counts.sqlite")
    assert not budget.limited
    assert all(budget.reserve() for _ in range(100))
    assert not (tmp_path / "sub").exists()


def test_daily_and_run_limits_are_enforced(tmp_path):
    path = tmp_path / "counts.sqlite"
    assert [RequestBudget(path, 2).reserve() for _ in range(3)] == [True, True, False]
    run = RequestBudget(tmp_path / "run.sqlite", None, run_id="pi-acceptance", run_limit=2)
    assert [run.reserve() for _ in range(3)] == [True, True, False]


@pytest.mark.parametrize("limit", [0, -1, 10_001, 1.5])
def test_invalid_budget_is_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        RequestBudget(tmp_path / "x.sqlite", limit)


def test_exhausted_budget_blocks_the_request_before_the_network(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHE_API_KEY", "test-key")
    monkeypatch.setenv("PSYCHE_VISION_MODEL", "grok-test")
    budget = RequestBudget(tmp_path / "counts.sqlite", 1)
    assert budget.reserve()
    reports = []
    provider = PsycheGrokVisionProvider(budget=budget, report=lambda kind, **fields: reports.append(fields))
    with pytest.raises(AssistantError, match="budget_exhausted"):
        provider.describe("What is ahead?", b"jpeg", "en")
    assert reports[0]["error"] == "budget_exhausted" and not reports[0]["success"]


def test_missing_key_does_not_consume_budget(tmp_path, monkeypatch):
    monkeypatch.delenv("PSYCHE_API_KEY", raising=False)
    budget = RequestBudget(tmp_path / "counts.sqlite", 1)
    provider = PsycheGrokVisionProvider(budget=budget)
    with pytest.raises(AssistantError, match="missing_key"):
        provider.describe("What is ahead?", b"jpeg", "en")
    assert budget.reserve()


def test_navigation_fetch_reports_and_respects_the_acceptance_budget(tmp_path):
    budget = RequestBudget(tmp_path / "counts.sqlite", None, run_id="nav", run_limit=1)
    assert budget.reserve()
    reports = []
    fetch = measured_amap_fetch(budget, lambda kind, **fields: reports.append((kind, fields)))
    with pytest.raises(OSError):
        fetch("https://example.invalid", 1)
    assert reports[0][0] == "navigation_request" and reports[0][1]["success"] is False


# K4 "can I walk ahead": the frame and the cane's range readings decide together.

class Ranges:
    """Stands in for PiNavigation: only the range report the assistant reads."""

    def __init__(self, front_m=None, front_blocked=False):
        self.report = {"front_m": front_m, "head_m": None, "front_blocked": front_blocked, "head_blocked": False}

    def range_report(self):
        return self.report


ZH = {"question_language": "zh", "answer_language": "zh", "destination_dialogue": False}


def walk_harness(monkeypatch, reply, ranges, *, config=None):
    seen = []

    def describe(question, jpeg, language, *, request_id=None, prompt=None):
        seen.append((question, prompt))
        if isinstance(reply, Exception):
            raise reply
        return reply

    harness = Harness(monkeypatch, describe, transcript="前面可以走吗？", navigation=ranges,
                      config={**ZH, "walkable_answers": True, **(config or {})})
    return harness, seen


@pytest.mark.parametrize("text", ["前面可以走吗？", "我能不能过去", "可以继续往前走吗", "这条路走得通吗", "前面有没有障碍",
                                  "can I walk ahead?"])
def test_walk_questions_are_recognised_in_mandarin_and_english(text):
    assert wake_assistant.route_command(text) == ("visual_safety", None)


@pytest.mark.parametrize("text", ["前面是什么？", "我左边有什么", "这是什么颜色", "你能看到前面走过来的人吗"])
def test_scene_questions_are_not_walk_questions(text):
    assert wake_assistant.route_command(text) == ("vision", None)


def test_walk_question_sends_the_frame_with_the_range_readings(monkeypatch):
    harness, seen = walk_harness(monkeypatch, "可以走，前面是空旷的走廊。后面还有一扇门。", Ranges(front_m=2.5))
    with harness as h:
        h.ask()
    assert seen == [("前面可以走吗？\n[手杖测距: 前方250厘米]", wake_assistant.WALK_PROMPT)]
    assert h.spoken == ["可以走，前面是空旷的走廊。"]


def test_silent_range_sensors_are_reported_as_no_reading(monkeypatch):
    harness, seen = walk_harness(monkeypatch, "不可以走，画面太暗。", Ranges())
    with harness as h:
        h.ask()
    assert seen[0][0] == "前面可以走吗？\n[手杖测距: 无读数]"


def test_close_reading_overrules_a_picture_that_looks_clear(monkeypatch):
    harness, _ = walk_harness(monkeypatch, "可以走，前面很空旷。", Ranges(front_m=0.45, front_blocked=True))
    with harness as h:
        h.ask()
    assert h.spoken == ["不可以走，手杖测到前方大约45厘米有障碍。"]


def test_close_reading_keeps_the_reply_that_names_the_obstacle(monkeypatch):
    harness, _ = walk_harness(monkeypatch, "不可以走，前面有一把椅子。", Ranges(front_m=0.45, front_blocked=True))
    with harness as h:
        h.ask()
    assert h.spoken == ["不可以走，前面有一把椅子。"]


def test_close_reading_answers_when_the_cloud_fails(monkeypatch):
    harness, _ = walk_harness(monkeypatch, AssistantError("provider_timeout"),
                              Ranges(front_m=0.45, front_blocked=True))
    with harness as h:
        h.ask()
    assert h.spoken == ["不可以走，手杖测到前方大约45厘米有障碍。"]


def test_cloud_failure_without_a_close_reading_is_reported(monkeypatch):
    harness, _ = walk_harness(monkeypatch, AssistantError("provider_timeout"), Ranges(front_m=2.5))
    with harness as h:
        h.ask()
    assert h.spoken == ["云端服务超时，请再试一次。"]


def test_walk_question_keeps_the_fixed_refusal_unless_enabled(monkeypatch):
    harness, seen = walk_harness(monkeypatch, "可以走。", Ranges(front_m=2.5), config={"walkable_answers": False})
    with harness as h:
        h.ask()
    assert not seen
    assert h.spoken == ["只凭一个画面，我无法判断能不能安全通过。"]


def shared_cane(monkeypatch, *, in_turn):
    from cane.app import CaneApp
    from cane.vision.navigation import SharedCane

    said, taps = [], []
    monkeypatch.setattr(CaneApp, "say", lambda self, prompt_id, priority=None, **options: said.append(prompt_id) or True)
    cane = object.__new__(SharedCane)
    cane.clock = lambda: cane.now
    cane.now, cane._warning_seen = 100.0, {}
    cane.cloud_assistant = SimpleNamespace(in_turn=lambda: in_turn, toggle=taps.append)
    return cane, said, taps


def test_fourth_key_short_press_asks_the_vision_question(monkeypatch):
    cane, _, taps = shared_cane(monkeypatch, in_turn=False)
    cane.on_button("mode", "short")
    cane.on_button("vision", "short")
    assert taps == ["vision_button", "vision_button"]


def test_same_range_warning_stays_quiet_during_a_turn(monkeypatch):
    cane, said, _ = shared_cane(monkeypatch, in_turn=True)
    for _ in range(40):  # two seconds of 20 Hz frames with the same obstacle
        cane.say("stop_now")
        cane.now += 0.05
    cane.say("obstacle_head")  # a new kind of warning still interrupts
    cane.now += 1.0
    cane.say("stop_now")  # the obstacle left and came back
    assert said == ["stop_now", "obstacle_head", "stop_now"]


def test_range_warning_repeats_outside_a_turn(monkeypatch):
    cane, said, _ = shared_cane(monkeypatch, in_turn=False)
    for _ in range(3):
        cane.say("stop_now")
        cane.now += 0.05
    assert said == ["stop_now"] * 3
