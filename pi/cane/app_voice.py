"""Optional offline microphone input and explicit text confirmation intents."""

from __future__ import annotations

import importlib.util
import logging
import time
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import assert_never

from .app_context import AppContext
from .app_types import SelectionResult, TextResult
from .config import Destination
from .model import Priority
from .prompts import destination_prompt_id
from .voice.asr import VOSK_MODELS, ASRInputError, ASRUnavailable
from .voice.destinations import TextIntentKind, asks_for_nearby, parse_text_intent, spoken_origin_query, spoken_place_query
from .voice.places import PlaceWorker, PlaceWorkerError, ProviderPlace, preset_choices, public_search_region
from .voice.recorder import RecorderUnavailable

log = logging.getLogger(__name__)


class VoiceMixin:
    def _voice_unavailable_reason(self: AppContext) -> str | None:
        app = self
        if not app.cfg.voice.enabled:
            return "disabled"
        if app.cfg.voice.backend != "vosk":
            return "text_input_only"
        if app.lang == "yue":
            return "cantonese_asr_unavailable"
        if importlib.util.find_spec("vosk") is None:
            return "vosk_not_installed"
        model = Path(app.cfg.voice.vosk_model_dir) / VOSK_MODELS[app.lang]
        if not model.is_dir():
            return "vosk_model_missing"
        if app.power.reduced:
            return "supply_load_reduction"
        return None

    def start_voice_input(self: AppContext) -> None:
        app = self
        with app._state_lock:
            if app.listening or app._stop.is_set():
                return
            reason = app._voice_unavailable_reason()
            if reason is not None:
                app.say("voice_unavailable", Priority.SYSTEM)
                app.logbook.write("voice", event="unavailable", reason=reason)
                return
            app.voice_wake.pause()
            app.voice_generation += 1
            generation = app.voice_generation
            record_stop = Event() if app.cfg.voice.manual_stop else None
            app.voice_record_stop = record_stop
            if app.cfg.voice.candidate_selection:
                app.pending_dest = None
                if not app.destination_only or app.cfg.voice.places_source != "apple":
                    app.destination_search.reset()
                app.arbiter.discard_information()
            app.say("voice_listening")
            app.listening = True

        def work() -> None:
            try:
                text = app._record_and_transcribe(generation, record_stop)
            except (ASRUnavailable, ASRInputError, RecorderUnavailable, OSError, wave.Error) as error:
                log.warning("voice input unavailable: %s", error)

                def failed() -> None:
                    if generation == app.voice_generation and not app._stop.is_set():
                        app._voice_failed()

                app._jobs.put(failed)
                return

            def apply() -> None:
                if generation == app.voice_generation and not app._stop.is_set():
                    if text is None:
                        app.cancel_voice_input()
                    else:
                        app.on_destination_text(text)

            app._jobs.put(apply)

        app._spawn(work, "voice")

    def _voice_failed(self: AppContext) -> None:
        app = self
        app.cancel_voice_input()
        app.say("voice_unavailable", Priority.SYSTEM)
        app.logbook.write("voice", event="failed", recognized=False)

    def stop_voice_input(self: AppContext) -> bool:
        with self._state_lock:
            if self.voice_record_stop is None or self.voice_record_stop.is_set():
                return False
            self.voice_record_stop.set()
            return True

    def cancel_voice_input(self: AppContext) -> None:
        app = self
        with app._state_lock:
            if app.voice_record_stop is not None:
                app.voice_record_stop.set()
            app.voice_record_stop = None
            app.voice_recording = False
            app.listening = False
            app.voice_generation += 1

    def _record_and_transcribe(
        self: AppContext, generation: int | None = None, record_stop: Event | None = None,
    ) -> str | None:
        app = self
        from .voice.asr import VoskRecognizer
        from .voice.recorder import (
            RecordingStoppedEarly,
            record_until_stopped,
            record_wav,
        )

        voice = app.cfg.voice
        language = app.lang
        generation = app.voice_generation if generation is None else generation
        phrases = [] if app.cfg.voice.candidate_selection and app.cfg.voice.places_script else [
            alias for destination in app.cfg.nav.destinations for alias in [destination.name, *destination.aliases]
        ]
        with TemporaryDirectory(prefix="voice-", dir=app.logbook.dir) as directory:
            with app.voice_wake.microphone:
                deadline = time.monotonic() + 3.0
                while "voice_listening" in app.arbiter.pending() or app.audio.busy():
                    if record_stop is not None and record_stop.is_set():
                        return None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RecorderUnavailable("destination recording prompt did not finish in time")
                    if app._stop.wait(min(0.05, remaining)):
                        return None
                with app._state_lock:
                    if app._stop.is_set() or generation != app.voice_generation:
                        return None
                    if record_stop is not None and record_stop.is_set():
                        return None
                    app.voice_recording = True
                try:
                    target = Path(directory) / "utterance.wav"
                    if record_stop is None:
                        recording = record_wav(target, voice.record_s, voice.device)
                    else:
                        recording = record_until_stopped(target, record_stop, voice.device)
                except RecordingStoppedEarly:
                    return None
                finally:
                    with app._state_lock:
                        if generation == app.voice_generation:
                            app.voice_recording = False
                            app.voice_record_stop = None
            if app._stop.is_set() or generation != app.voice_generation:
                return None
            recognizer = VoskRecognizer(voice.vosk_model_dir, language, phrases)
            return recognizer.transcribe(recording)

    def on_destination_text(self: AppContext, text: str) -> TextResult:
        """A local text boundary also accepts explicit confirm/cancel/retry."""
        app = self
        with app._state_lock:
            app.cancel_voice_input()
            origin_query = spoken_origin_query(text)
            if origin_query is not None and app.destination_only and app.cfg.voice.places_source == "apple":
                return app.search_origin(origin_query)
            intent = parse_text_intent(text, app.cfg.nav.destinations)
            app.logbook.write("voice", intent=intent.kind.value,
                               match=intent.destination.id if intent.destination else None)
            match intent.kind:
                case TextIntentKind.CONFIRM:
                    result = app.enqueue_control("confirm")
                    return {"matched": None, "action": "confirm", "accepted": False} if not result["accepted"] else {
                        "matched": None, "action": "confirm",
                    }
                case TextIntentKind.CANCEL:
                    app.enqueue_control("cancel_destination" if app.pending_dest or app.destination_search.id
                                        else "stop_navigation")
                    return {"matched": None, "action": "cancel"}
                case TextIntentKind.RETRY:
                    app.pending_dest = None
                    app.destination_search.reset()
                    app.arbiter.discard_information()
                    return {"matched": None, "action": "retry"}
                case TextIntentKind.UNKNOWN | TextIntentKind.DESTINATION:
                    app.pending_dest = None
                    app.destination_search.reset()
                    app.arbiter.discard_information()
                    if app.cfg.voice.candidate_selection:
                        query = spoken_place_query(text)
                        if query is not None:
                            return app._search_destination(query, ask_origin=asks_for_nearby(text))
                    destination = intent.destination
                    if destination is None:
                        app.say("voice_not_understood")
                        return {"matched": None}
                    app._stage_destination(destination)
                    return {"matched": destination.id, "name": destination.name}
                case unreachable:
                    assert_never(unreachable)

    def _search_destination(self: AppContext, query: str, *, ask_origin: bool = False) -> TextResult:
        voice = self.cfg.voice
        worker = PlaceWorker(voice.places_python, Path(voice.places_script)) if voice.places_script else None
        current = self.destination_search
        search_id = current.begin(query, worker)
        if worker is None:
            current.finish(search_id, preset_choices(query, self.cfg.nav.destinations))
        else:
            apple_search = self.destination_only and voice.places_source == "apple"
            city = "" if apple_search else self.cfg.nav.city
            origin = self._search_origin_point() if apple_search else None
            if apple_search and ask_origin and origin is None:
                current.state = "waiting_origin"
                self.say("choose_search_origin", Priority.SYSTEM)
                return {"matched": None, "accepted": True, "search_state": current.state}
            region = public_search_region(origin) if origin is not None else None

            def work() -> None:
                try:
                    choices = worker.search(query, city, nearby=region is not None, region=region)
                    if region is not None and not choices:
                        choices = worker.search(query, city)
                except PlaceWorkerError:
                    self._jobs.put(lambda: current.finish(search_id, None))
                else:
                    def apply() -> None:
                        current.finish(search_id, choices, origin)

                    self._jobs.put(apply)

            self._spawn(work, "place-search")
        return {"matched": None, "accepted": True, "search_state": current.state}

    def _search_origin_point(self: AppContext) -> tuple[float, float] | None:
        if self.manual_search_origin is not None:
            return self.manual_search_origin.lng, self.manual_search_origin.lat
        if self.location is not None and self._location_fresh():
            return self.location[0], self.location[1]
        return None

    def search_origin(self: AppContext, text: str) -> TextResult:
        """Look up possible departure points before applying a location change."""
        query = spoken_place_query(text)
        voice = self.cfg.voice
        if query is None or not self.destination_only or voice.places_source != "apple" or not voice.places_script:
            return {"matched": None, "accepted": False}
        worker = PlaceWorker(voice.places_python, Path(voice.places_script))
        with self._state_lock:
            current = self.origin_search
            search_id = current.begin(query, worker)

        def work() -> None:
            try:
                results = worker.search(query, "")
            except PlaceWorkerError:
                self._jobs.put(lambda: current.finish(search_id, None))
            else:
                self._jobs.put(lambda: current.finish(search_id, results))

        self._spawn(work, "origin-search")
        return {"matched": None, "accepted": True, "action": "origin_search", "search_state": "searching"}

    def select_origin(self: AppContext, search_id: str, candidate_id: str) -> SelectionResult:
        app = self
        with app._state_lock:
            choice = app.origin_search.choice(search_id, candidate_id)
            if not isinstance(choice, ProviderPlace) or choice.coordinate_frame != "WGS84":
                return {"accepted": False, "error": "起点候选已变化，请重新选择"}
            app.manual_search_origin = choice
            app.origin_search.reset()
            app.pending_dest = None
            if app.destination_search.query:
                app._search_destination(app.destination_search.query)
            return {"accepted": True, "name": choice.name}

    def use_phone_origin(self: AppContext) -> SelectionResult:
        app = self
        if not app.destination_only or app.cfg.voice.places_source != "apple":
            return {"accepted": False, "error": "请在语音目的地页切换起点"}
        with app._state_lock:
            app.manual_search_origin = None
            app.origin_search.reset()
            app.pending_dest = None
            if app.destination_search.query:
                app._search_destination(app.destination_search.query, ask_origin=True)
        return {"accepted": True, "name": "iPhone 当前位置"}

    def select_destination(self: AppContext, search_id: str, candidate_id: str) -> SelectionResult:
        """Stage only the place selected from the current candidates."""
        with self._state_lock:
            current = self.destination_search
            if self.listening:
                return {"accepted": False, "error": "请先结束录音"}
            choice = current.choice(search_id, candidate_id)
            if choice is None:
                return {"accepted": False, "error": "stale_or_unknown_choice"}
            worker = current.worker
            if isinstance(choice, ProviderPlace):
                if not self.destination_only or choice.coordinate_frame != "WGS84":
                    return {"accepted": False, "error": "unsupported_place_source"}
                self._stage_destination(Destination(choice.id, choice.name, lng=choice.lng, lat=choice.lat,
                                                    address=choice.address, coordinate_frame="WGS84"))
                current.state = "selected"
                return {"accepted": True, "name": choice.name}
            if worker is None:
                destination = next((d for d in self.cfg.nav.destinations if d.id == choice.id), None)
                if destination is None:
                    return {"accepted": False, "error": "stale_or_unknown_choice"}
                self._stage_destination(destination)
                current.state = "selected"
                return {"accepted": True, "name": choice.name}
            current.state = "resolving"

            def work() -> None:
                try:
                    place = worker.detail(choice.id)
                except PlaceWorkerError:
                    destination = None
                else:
                    destination = Destination(place.id, choice.name, lng=place.lng, lat=place.lat,
                                              address=place.address, coordinate_frame=place.coordinate_frame)

                def apply() -> None:
                    if current.id != search_id or current.state != "resolving":
                        return
                    if destination is None:
                        current.state = "error"
                        return
                    self._stage_destination(destination)
                    current.state = "selected"

                self._jobs.put(apply)

            self._spawn(work, "place-detail")
            return {"accepted": True, "name": choice.name}

    def _stage_destination(self: AppContext, destination: Destination) -> None:
        app = self
        app.pending_dest = destination
        app.arbiter.discard_information()
        if app.destination_only:
            return
        if destination.coordinate_frame == "WGS84":
            _ = app.say(destination_prompt_id(destination.id), Priority.INFO, extra=["dest_confirm"])
        else:
            _ = app.say("dest_confirm", Priority.INFO)
