"""Hand the microphone between the idle wake listener and short recordings."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from threading import Event, Lock
from typing import TYPE_CHECKING

from .asr import ASRUnavailable
from .recorder import RecorderUnavailable
from .wake import WakeWordListener

if TYPE_CHECKING:
    from ..app_context import AppContext


@dataclass(frozen=True, slots=True)
class WakeSettings:
    model_dir: str
    language: str
    phrase: str
    device: str


class WakeSchedule:
    """Run one cancellable capture worker, returning results to the main loop."""

    def __init__(self) -> None:
        self.microphone = Lock()
        self._stop: Event | None = None
        self._settings: WakeSettings | None = None
        self._listener: WakeWordListener | None = None
        self._retry_at = 0.0

    def pause(self) -> None:
        """Give a user-initiated recording priority over ambient listening."""
        if self._stop is not None:
            self._stop.set()

    def awaiting_retry(self, now: float) -> bool:
        return now < self._retry_at

    def tick(self, app: AppContext) -> None:
        """Listen while opted in and the microphone is idle."""
        cfg = app.cfg.voice
        eligible = (
            cfg.wake_enabled and not app.sim and not app._stop.is_set()
            and not app.listening and not app.audio.busy() and not app.navigator.active
            and not app._route_pending and not app.arbiter.safety_hold_active()
            and platform.system() in {"Linux", "Darwin"} and app._voice_unavailable_reason() is None
        )
        if not eligible:
            self.pause()
            return
        if self._stop is not None or app.clock() < self._retry_at:
            return
        stop = Event()
        settings = WakeSettings(cfg.vosk_model_dir, app.lang, cfg.wake_phrase, cfg.device)
        self._stop = stop

        def work() -> None:
            try:
                if self._settings != settings or self._listener is None:
                    self._listener = WakeWordListener(settings.model_dir, settings.language, settings.phrase)
                    self._settings = settings
                triggered = self._listener.listen(
                    enabled=True, device=settings.device, stop=stop,
                    microphone=self.microphone, on_wake=lambda: None,
                )
            except (ASRUnavailable, RecorderUnavailable, OSError, RuntimeError):
                app._jobs.put(lambda: self._complete(app, stop, triggered=False, failed=True))
            else:
                app._jobs.put(lambda: self._complete(app, stop, triggered=triggered, failed=False))

        app._spawn(work, "voice-wake")

    def _complete(self, app: AppContext, stop: Event, *, triggered: bool, failed: bool) -> None:
        if self._stop is not stop:
            return
        self._stop = None
        if failed:
            self._retry_at = app.clock() + 5
            app.logbook.write("voice", event="wake_unavailable")
        if triggered and not stop.is_set() and not app._stop.is_set() and app.cfg.voice.wake_enabled:
            app.start_voice_input()
