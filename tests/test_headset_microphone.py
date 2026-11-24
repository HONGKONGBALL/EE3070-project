from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from cane.vision import voice_audio
from cane.vision.wake_assistant import AssistantError, WakeAssistant


def node(name, device_id):
    return {"name": name, "description": name, "properties": {"device.id": device_id}}


@pytest.fixture
def pulse(monkeypatch):
    backend = voice_audio.PulseAudio()
    nodes = {
        "sinks": [node("airpods", "84"), node("hyperx", "57"), node("speakers", "41")],
        "sources": [node("webcam", "48"), node("airpods.monitor", "84"),
                    node("airpods-mic", "84"), node("hyperx-mic", "57")],
    }
    monkeypatch.setattr(backend, "_nodes", nodes.__getitem__)
    monkeypatch.setattr(voice_audio, "local_audio", lambda: backend)
    return backend


def test_recording_uses_current_playback_device(pulse, monkeypatch):
    @contextmanager
    def stream(device):
        yield lambda _timeout: device.id

    monkeypatch.setattr(pulse, "stream", stream)
    monkeypatch.setattr(voice_audio.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(stdout="airpods\n"))
    audio = SimpleNamespace(sink=SimpleNamespace(target=""))
    assistant = WakeAssistant({"microphone": "follow-output"}, None, audio)
    with assistant.microphone() as read:
        assert read(0) == "airpods-mic"
    audio.sink.target = "hyperx"
    with assistant.microphone() as read:
        assert read(0) == "hyperx-mic"


def test_output_without_mic_does_not_select_the_webcam(pulse):
    audio = SimpleNamespace(sink=SimpleNamespace(target="speakers"))
    assistant = WakeAssistant({"microphone": "follow-output"}, None, audio)
    with pytest.raises(AssistantError, match="microphone_unavailable"):
        with assistant.microphone():
            pytest.fail("A capture stream should not open")
