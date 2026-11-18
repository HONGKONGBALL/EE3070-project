"""Synchronous safety loop; adapters return work through bounded queues.

The Mac host and the original Pi launcher both import CaneApp here. Sensing,
controls, navigation and status live in separate modules so safety does not wait
on route requests, voice recording, the web server or camera scans.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .app_controls import ControlsMixin
from .app_navigation import NavigationMixin
from .app_sensing import SensingMixin
from .app_status import StatusMixin
from .app_types import ControlState, ImuSource, QueuedControl, ScanStatus
from .app_voice import VoiceMixin
from .arbiter import ArbiterEvent, AudioSink, OutputArbiter
from .audio import AplayAudio, ConsoleAudio, load_durations
from .config import Config, Destination
from .hazard import HazardAssessor
from .logbook import Logbook
from .model import Direction, OutputRequest, Priority, SensorFrame
from .nav.amap import AmapClient, RouteClient
from .nav.navigator import Navigator, NavOutput
from .pico_link import PicoLinkBase, SerialPicoLink, SimPicoLink
from .power import PowerPolicy
from .prompts import CATALOG, longest_duration
from .state_machine import CaneStateMachine
from .terrain import RoughnessClassifier
from .voice.places import DestinationSearch, ProviderPlace
from .voice.pi_places import PiPlaceSearch
from .voice.wake_schedule import WakeSchedule

if TYPE_CHECKING:
    from .vision.runner import VisionRunner

log = logging.getLogger(__name__)
LOCATION_STALE_S = 15.0
VSYS_LOW_V = 4.5  # compatibility: a supply threshold, never battery state-of-charge


class CaneApp(ControlsMixin, NavigationMixin, SensingMixin, VoiceMixin, StatusMixin):
    """Mutable controller owned by the main loop; HTTP actions are queued."""

    def __init__(
        self,
        cfg: Config,
        sim: bool = False,
        pico: PicoLinkBase | None = None,
        audio: AudioSink | None = None,
        amap: RouteClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        imu: ImuSource | None = None,
        start_threads: bool = True,
        destination_only: bool = False,
        pi_places: PiPlaceSearch | None = None,
    ) -> None:
        self.cfg, self.sim, self.clock = cfg, sim, clock
        self.destination_only = destination_only
        self.pi_places = pi_places
        self.lang = cfg.general.language
        self.test_mode = cfg.general.test_mode
        self.logbook = Logbook(cfg.general.log_dir)
        durations = load_durations(cfg.audio.dir)
        clip_s: dict[str, float] = {}
        for pid in CATALOG:
            try:
                clip_s[pid] = longest_duration(durations, pid)
            except KeyError:
                continue
        if audio is None:
            audio = (ConsoleAudio(self.lang, durations.get(self.lang)) if sim
                     else AplayAudio(cfg.audio.dir, self.lang, cfg.audio.device, cfg.audio.player))
        self.audio = audio
        self.pico = pico or (SimPicoLink() if sim else SerialPicoLink(cfg.pico.port, cfg.pico.baud))
        self.arbiter = OutputArbiter(audio, self.pico.vibrate, cfg.arbiter.safety_hold_s,
                                     cfg.arbiter.repeat_cooldown_s, clock, record_event=self._log_output)
        self.sm = CaneStateMachine(clock)
        self.sm.test_mode = self.test_mode
        self.sm.on_transition(lambda tr: self.logbook.write(
            "state", src=tr.src.value, dst=tr.dst.value, event=tr.event))
        self.hazards = HazardAssessor(cfg.hazard, cfg.tfmini, clip_s)
        self.navigator = Navigator(cfg.nav)
        self.roughness = RoughnessClassifier()
        self.power = PowerPolicy()
        if amap is None and cfg.nav.enabled and cfg.secrets.amap_key:
            amap = AmapClient(cfg.secrets.amap_key)
        self.amap = amap
        self.imu = imu
        self.vision: VisionRunner | None = None
        self._coord_offset: tuple[float, float] | None = None
        self._jobs: queue.Queue[Callable[[], None]] = queue.Queue()
        self._controls: queue.Queue[QueuedControl] = queue.Queue(maxsize=128)
        self._state_lock = threading.RLock()
        self._start_threads = start_threads
        self.frame: SensorFrame | None = None
        self.tf_window: deque[float] = deque(maxlen=40)
        self.last_hazards: list[str] = []
        self.terrain_class = "unknown"
        self.last_scan: ScanStatus | None = None
        self._last_scan_t = 0.0
        self.location: tuple[float, float, float, float] | None = None
        self.pending_dest: Destination | None = None
        self.destination_search = DestinationSearch()
        self.origin_search = DestinationSearch()
        self.manual_search_origin: ProviderPlace | None = None
        self.voice_wake = WakeSchedule()
        self.voice_generation = 0
        self.last_nav: NavOutput | None = None
        self._last_nav_t = 0.0
        self._route_generation = 0
        self._route_pending = False
        self.vsys: float | None = None
        self.listening = False
        self.voice_recording = False
        self.voice_record_stop: threading.Event | None = None
        self.help_requested = False
        self.fault_reasons: set[str] = set()
        self.last_control: ControlState | None = None
        self._last_hb = 0.0
        self._pico_ok = False
        self._started_at = clock()
        self._last_assessed_t: float | None = None
        self._invalid_range_frames = 0
        self._last_scan_entered = -1.0
        self._scan_generation = 0
        self._light_on = False
        self._stop = threading.Event()

    @property
    def nearby_search(self) -> bool:
        return self.pi_places is not None or (self.destination_only and self.cfg.voice.places_source == "apple")

    def say(self, prompt_id: str, priority: Priority | None = None, *,
            extra: list[str] | None = None, direction: Direction = Direction.NONE,
            text: str | None = None) -> bool:
        """All audio and haptics enter the safety arbiter."""
        prompt = CATALOG.get(prompt_id)
        prio = priority if priority is not None else (prompt.priority if prompt else Priority.INFO)
        self.arbiter.set_orientation_ok(self.sm.turns_allowed and self._pico_ok)
        return self.arbiter.submit(OutputRequest(
            prompt_id, prio, direction, prompt.vibration if prompt else None, text, extra or []))

    def start(self) -> None:
        self._started_at = self.clock()
        self.pico.start()
        self.sm.handle("boot_done")
        self.say("boot_ready")
        if self.test_mode:
            self.say("test_mode_on")
        self.logbook.write("boot", sim=self.sim, lang=self.lang, test_mode=self.test_mode)

    def run(self, hz: float = 20.0) -> None:
        self.start()
        period = 1.0 / hz
        while not self._stop.is_set():
            started = self.clock()
            self.step()
            self._stop.wait(max(0.0, period - (self.clock() - started)))

    def stop(self) -> None:
        self._stop.set()
        self.cancel_voice_input()
        self.voice_wake.pause()
        self._route_generation += 1
        self.pico.close()

    def step(self) -> None:
        """Assess the newest observations before executing UI or network jobs."""
        with self._state_lock:
            now = self.clock()
            if now - self._last_hb >= self.cfg.pico.heartbeat_s:
                self.pico.heartbeat()
                self._last_hb = now
            for _ in range(256):
                try:
                    msg = self.pico.inbox.get_nowait()
                except queue.Empty:
                    break
                self._on_pico(msg)
            if not self.destination_only:
                self._check_pico(now)
                self._update_motion()
                self._run_hazards()
                self._check_location(now)
            for _ in range(32):
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                job()
            self._drain_controls()
            self.arbiter.set_orientation_ok(self.sm.turns_allowed and self._pico_ok)
            self.arbiter.tick()
            if self._start_threads:
                self.voice_wake.tick(self)

    def _spawn(self, fn: Callable[[], None], name: str) -> None:
        if self._start_threads:
            threading.Thread(target=fn, daemon=True, name=name).start()
        else:
            fn()

    def _log_output(self, event: ArbiterEvent) -> None:
        self.logbook.write("output", action=event.action, prompt_id=event.prompt_id,
                           priority=event.priority, monotonic_t=event.t)

    def log_files(self) -> list[Path]:
        return self.logbook.files()
