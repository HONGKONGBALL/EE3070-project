"""Web controls can invoke local capture/ASR without browser speech services."""

from pathlib import Path
from threading import Event

from cane.voice import asr, recorder

from test_app import feed

pytest_plugins = ("test_app",)


def test_voice_input_control_uses_local_recorder_and_destination_confirmation(app, audio, clock, monkeypatch):
    # Given local capture/ASR seams, with no real microphone opened by the test.
    captured: list[Path] = []

    def record(path: str | Path, seconds: float, device: str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"local fixture recording")
        captured.append(target)
        return target

    class LocalRecognizer:
        def __init__(self, model_dir: str | Path, lang: str, phrases: list[str]) -> None:
            assert lang == "cmn"
            assert "香港城市大学" in phrases

        def transcribe(self, path: Path) -> str:
            assert path.read_bytes() == b"local fixture recording"
            return "香港 城市 大学"

    monkeypatch.setattr(recorder, "record_wav", record)
    monkeypatch.setattr(asr, "VoskRecognizer", LocalRecognizer)
    monkeypatch.setattr(app, "_voice_unavailable_reason", lambda: None)
    monkeypatch.setattr(audio, "busy", lambda: False)
    feed(app, clock)
    # When the web control enters the serialized application loop.
    result = app.enqueue_control("voice_input")
    feed(app, clock, n=2)
    # Then local recording is used and the destination still waits for OK.
    assert result["accepted"]
    assert len(captured) == 1
    assert app.pending_dest is not None and app.pending_dest.id == "dest1"
    assert not app.navigator.active


def test_voice_input_control_reports_cantonese_unavailable(app, audio, clock):
    # Given Cantonese output with local offline voice input selected.
    app.lang = "yue"
    app.cfg.voice.backend = "vosk"
    feed(app, clock)
    audio.finish()
    # When local voice input is requested.
    result = app.enqueue_control("voice_input")
    feed(app, clock)
    # Then no recording starts and local unavailability is announced.
    assert result["accepted"]
    assert not app.listening
    assert ["voice_unavailable"] in audio.played


def test_second_destination_press_stops_manual_capture(app):
    app.cfg.voice.manual_stop = True
    app.listening = True
    app.voice_recording = True
    stop = Event()
    app.voice_record_stop = stop

    app.on_button("dest", "short")

    assert stop.is_set()
    assert app.listening


def test_web_voice_stop_is_applied_by_main_loop(app):
    app.cfg.voice.manual_stop = True
    stop = Event()
    app.voice_record_stop = stop

    assert app.enqueue_control("voice_stop")["accepted"]
    assert not stop.is_set()
    app.step()
    assert stop.is_set()
