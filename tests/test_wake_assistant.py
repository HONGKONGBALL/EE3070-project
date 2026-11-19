from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

import pytest

from cane.arbiter import OutputArbiter
from cane.model import OutputRequest, Priority
from cane.vision.assistant_provider import AssistantError
from cane.vision.voice_commands import MockNavigationAdapter, confirmed, route_command
from cane.vision.wake_assistant import WakeAssistant
from cane.vision.wake_word import PRONUNCIATION, WakeDetector


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
    ("Navigate to", ("unsupported_navigation", None)),
    ("What is in front of me?", ("vision", None)),
])
def test_voice_routes(text, expected):
    assert route_command(text) == expected


def test_route_requires_affirmation_and_mock_does_not_claim_success():
    assert confirmed("Yes, please!")
    assert confirmed("Yes.") and not confirmed("yes but wait")
    assert not MockNavigationAdapter().plan_route("CityU Library").success


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
