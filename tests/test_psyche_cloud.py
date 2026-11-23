"""Cloud (psyche_grok) assistant: K4/K2 taps, spoken failures, ACK retry and request budget."""
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


class Harness:
    """A WakeAssistant in cloud mode with fake audio, microphone, speech and provider."""

    def __init__(self, monkeypatch, describe=None, *, rejected_cues=0, transcript="what is in front of me",
                 navigation=None, config=None, chat=None):
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
        self.assistant = WakeAssistant({"mode": "psyche_grok", **(config or {})}, object(), audio,
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

    def __enter__(self):
        self.assistant.start()
        return self

    def __exit__(self, *exc):
        self.assistant.close()


def test_two_k4_taps_ask_one_cloud_question_and_speak_the_short_reply(monkeypatch):
    with Harness(monkeypatch) as h:
        h.ask()
        assert h.assistant.state == "PASSIVE_LISTENING" and not h.assistant.error
    assert h.questions == ["what is in front of me"]
    assert h.spoken == ["A chair is ahead. A lamp is behind it."]


def test_k4_bounce_and_taps_during_processing_cannot_open_another_turn(monkeypatch):
    assistant = Harness(monkeypatch).assistant
    assert assistant.toggle("vision_button")
    request_id = assistant.request_id
    assert not assistant.toggle("vision_button")
    assistant.button_at -= 1
    assistant._state("PROCESSING")
    assert not assistant.toggle("vision_button")
    assert not assistant.toggle("navigation_button")
    assert assistant.request_id == request_id


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
    assert h.spoken == ["A chair is ahead. A lamp is behind it."]


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


# K4 movement questions translate model labels and real readings into local descriptions.

class Ranges:
    """Stands in for PiNavigation: only the range report the assistant reads."""

    def __init__(self, front_m=None, front_blocked=False):
        self.report = {"front_m": front_m, "head_m": None, "front_blocked": front_blocked, "head_blocked": False}

    def range_report(self):
        return self.report


ZH = {"question_language": "zh", "answer_language": "zh", "destination_dialogue": False}


def walk_harness(monkeypatch, reply, ranges, *, config=None, transcript="Can I move forward?"):
    seen = []

    def describe(question, jpeg, language, *, request_id=None, prompt=None):
        seen.append((question, jpeg, language, prompt))
        if isinstance(reply, Exception):
            raise reply
        return reply

    harness = Harness(monkeypatch, describe, transcript=transcript, navigation=ranges,
                      config={"walkable_answers": True, **(config or {})})
    return harness, seen


@pytest.mark.parametrize("text", ["前面可以走吗？", "我能不能过去", "可以继续往前走吗", "这条路走得通吗", "前面有没有障碍",
                                  "can I walk ahead?", "Can I move forward?", "Is it safe to go forward?",
                                  "Is it safe for me to move forward?", "Can I cross the road?"])
def test_walk_questions_are_recognised_in_mandarin_and_english(text):
    assert wake_assistant.route_command(text) == ("visual_safety", None)


@pytest.mark.parametrize("text", ["前面是什么？", "我左边有什么", "这是什么颜色", "你能看到前面走过来的人吗"])
def test_scene_questions_are_not_walk_questions(text):
    assert wake_assistant.route_command(text) == ("vision", None)


def test_walk_question_reports_a_visible_observation_and_uncertainty(monkeypatch):
    harness, seen = walk_harness(monkeypatch, "chair", Ranges(front_m=2.5))
    with harness as h:
        h.ask()
    assert seen == [("Can I move forward?", b"jpeg", "en", wake_assistant.WALK_PROMPT)]
    assert h.spoken == ["I can see a chair ahead. I can't tell from one view whether it's safe to proceed."]


@pytest.mark.parametrize("model_reply", ["none", "Yes, you can proceed.", "You can cross now."])
def test_open_looking_scene_without_measurements_does_not_clear_the_path(monkeypatch, model_reply):
    harness, _ = walk_harness(monkeypatch, model_reply, Ranges())
    with harness as h:
        h.ask()
    assert h.spoken == ["Distance sensing is unavailable. I can't tell from one view whether it's safe to proceed."]


def test_close_reading_returns_without_a_cloud_request(monkeypatch):
    harness, seen = walk_harness(monkeypatch, "none", Ranges(front_m=0.45, front_blocked=True))
    with harness as h:
        h.ask()
    assert seen == []
    assert h.spoken == ["The cane detects something about 45 centimetres ahead. Please use caution."]


@pytest.mark.parametrize("reply", ["none", AssistantError("provider_timeout")])
def test_new_close_reading_takes_precedence_during_request(monkeypatch, reply):
    ranges = Ranges(front_m=2.5)
    harness, _ = walk_harness(monkeypatch, reply, ranges)
    original = harness.assistant.provider.describe

    def describe(*args, **kwargs):
        ranges.report.update(front_m=0.45, front_blocked=True)
        return original(*args, **kwargs)

    harness.assistant.provider.describe = describe
    with harness as h:
        h.ask()
    assert h.spoken == ["The cane detects something about 45 centimetres ahead. Please use caution."]


def test_cloud_failure_without_a_close_reading_is_reported(monkeypatch):
    harness, _ = walk_harness(monkeypatch, AssistantError("provider_timeout"), Ranges(front_m=2.5))
    with harness as h:
        h.ask()
    assert h.spoken == [CLOUD_FAILURE_SPEECH["provider_timeout"]]


def test_walk_question_keeps_the_fixed_refusal_unless_enabled(monkeypatch):
    harness, seen = walk_harness(monkeypatch, "可以走。", Ranges(front_m=2.5), config={"walkable_answers": False})
    with harness as h:
        h.ask()
    assert not seen
    assert h.spoken == ["I can't tell from one view whether it's safe to proceed."]


@pytest.mark.parametrize("issue", ["camera_quality", "camera_unavailable"])
def test_unusable_camera_stays_local(monkeypatch, issue):
    harness, seen = walk_harness(monkeypatch, "none", Ranges())

    def no_frame(camera):
        raise AssistantError(issue)

    monkeypatch.setattr(wake_assistant, "shared_jpeg", no_frame)
    harness.assistant._turn("vision_button", b"\x00\x10\x00\xf0" * 320)
    assert seen == []
    assert harness.spoken == [wake_assistant.VISION_RETRY if issue == "camera_quality"
                              else "The camera view is unavailable."]


def test_k4_uses_english_while_k2_keeps_its_mandarin_transcript(monkeypatch):
    destinations, requests = [], []

    def describe(question, jpeg, language, *, request_id):
        requests.append((question, language))
        return "A chair is ahead."

    nav = SimpleNamespace(submit_destination=destinations.append)
    with Harness(monkeypatch, describe, transcript="圖書館", navigation=nav,
                 config={**ZH, "vision_language": "en"}) as h:
        h.assistant.vision_asr = SimpleNamespace(transcribe=lambda pcm: "What's on my left?")
        h.ask()
        h.ask("navigation_button")
    assert requests == [("What's on my left?", "en")]
    assert destinations == ["圖書館"]
    assert h.assistant.status()["answer_language"] == "en"


def test_visible_concerns_receive_only_valid_sensor_observations(monkeypatch):
    received = []

    def describe(question, jpeg, language, *, request_id, prompt):
        received.append((question, prompt))
        return "The cane detects something about 45 centimetres ahead."

    question = "Do you notice any obstacles ahead?"
    harness = Harness(monkeypatch, describe, transcript=question, navigation=Ranges(front_m=0.45))
    harness.assistant._turn("vision_button", b"\x00\x10\x00\xf0" * 320)
    assert received == [(question, wake_assistant.SYSTEM_PROMPT + " Valid cane measurements: ahead 45 cm.")]
    assert harness.spoken == ["The cane detects something about 45 centimetres ahead."]


def test_ordinary_reply_cannot_explicitly_clear_the_route(monkeypatch):
    harness = Harness(monkeypatch, lambda *args, **kwargs: "You can safely cross the road.")
    harness.assistant._turn("vision_button", b"\x00\x10\x00\xf0" * 320)
    assert harness.spoken == ["I can't tell from one view whether it's safe to proceed."]


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
