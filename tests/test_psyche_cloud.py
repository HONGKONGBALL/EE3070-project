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
