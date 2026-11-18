"""Structural contract for the controller's cohesive service mixins."""

from __future__ import annotations

import queue
import threading
from _thread import LockType
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .app_types import (
    ControlAction,
    ControlResult,
    ControlState,
    ImuSource,
    QueuedControl,
    ScanStatus,
    SelectionResult,
    TextResult,
)
from .arbiter import AudioSink, OutputArbiter
from .config import Config, Destination
from .hazard import HazardAssessor
from .logbook import Logbook
from .model import Direction, Priority, SensorFrame
from .nav.amap import RouteClient
from .nav.navigator import Navigator, NavOutput
from .pico_link import PicoLinkBase
from .power import PowerPolicy
from .state_machine import CaneStateMachine
from .terrain import RoughnessClassifier, VibrationFeatures
from .vision.runner import VisionRunner
from .vision.stereo import ScanResult
from .voice.places import DestinationSearch, ProviderPlace


class WakeControl(Protocol):
    """Microphone coordination shared by the control and recording code."""

    microphone: LockType

    def pause(self) -> None: ...

    def awaiting_retry(self, now: float) -> bool: ...


class AppContext(Protocol):
    """Mutable state is owned by CaneApp.__init__, never by a service mixin."""

    cfg: Config
    sim: bool
    destination_only: bool
    clock: Callable[[], float]
    lang: str
    test_mode: bool
    logbook: Logbook
    audio: AudioSink
    pico: PicoLinkBase
    arbiter: OutputArbiter
    sm: CaneStateMachine
    hazards: HazardAssessor
    navigator: Navigator
    roughness: RoughnessClassifier
    power: PowerPolicy
    amap: RouteClient | None
    imu: ImuSource | None
    vision: VisionRunner | None
    frame: SensorFrame | None
    tf_window: deque[float]
    last_hazards: list[str]
    terrain_class: str
    last_scan: ScanStatus | None
    location: tuple[float, float, float, float] | None
    pending_dest: Destination | None
    destination_search: DestinationSearch
    origin_search: DestinationSearch
    manual_search_origin: ProviderPlace | None
    voice_generation: int
    voice_recording: bool
    voice_record_stop: threading.Event | None
    last_nav: NavOutput | None
    vsys: float | None
    listening: bool
    help_requested: bool
    fault_reasons: set[str]
    last_control: ControlState | None
    _coord_offset: tuple[float, float] | None
    _jobs: queue.Queue[Callable[[], None]]
    _controls: queue.Queue[QueuedControl]
    _state_lock: threading.RLock
    _last_nav_t: float
    _route_generation: int
    _route_pending: bool
    _last_scan_t: float
    _pico_ok: bool
    _started_at: float
    _last_assessed_t: float | None
    _invalid_range_frames: int
    _last_scan_entered: float
    _scan_generation: int
    _light_on: bool
    _stop: threading.Event

    @property
    def voice_wake(self) -> WakeControl: ...

    def say(self, prompt_id: str, priority: Priority | None = None, *,
            extra: list[str] | None = None, direction: Direction = Direction.NONE,
            text: str | None = None) -> bool: ...
    def enqueue_control(self, action: str) -> ControlResult: ...
    def _execute_control(self, action: ControlAction) -> None: ...
    def _confirm(self) -> None: ...
    def _pause(self, announce: bool = True) -> None: ...
    def _cancel_pending_route_for_pause(self) -> None: ...
    def _resume_blocked_reasons(self) -> list[str]: ...
    def repeat_last_nav(self) -> None: ...
    def cycle_language(self) -> None: ...
    def start_voice_input(self) -> None: ...
    def stop_voice_input(self) -> bool: ...
    def cancel_voice_input(self) -> None: ...
    def _voice_unavailable_reason(self) -> str | None: ...
    def _voice_failed(self) -> None: ...
    def _record_and_transcribe(
        self, generation: int | None = None, record_stop: threading.Event | None = None,
    ) -> str | None: ...
    def on_destination_text(self, text: str) -> TextResult: ...
    def select_destination(self, search_id: str, candidate_id: str) -> SelectionResult: ...
    def search_origin(self, text: str) -> TextResult: ...
    def select_origin(self, search_id: str, candidate_id: str) -> SelectionResult: ...
    def use_phone_origin(self) -> SelectionResult: ...
    def _search_origin_point(self) -> tuple[float, float] | None: ...
    def _search_destination(self, query: str, *, ask_origin: bool = False) -> TextResult: ...
    def _stage_destination(self, destination: Destination) -> None: ...
    def start_navigation(self, destination: Destination) -> None: ...
    def stop_navigation(self, announce: bool = True) -> None: ...
    def _location_fresh(self) -> bool: ...
    def _on_location(self, lng: float, lat: float, acc: float, timestamp: float) -> None: ...
    def _to_amap(self, lng: float, lat: float) -> tuple[float, float]: ...
    def _to_amap_fast(self, lng: float, lat: float) -> tuple[float, float]: ...
    def _handle_nav_outputs(self, outputs: list[NavOutput]) -> None: ...
    def _request_route(self, destination: tuple[float, float], reroute: bool) -> None: ...
    def _request_route_blocking(self, destination: tuple[float, float], reroute: bool,
                                generation: int | None = None) -> None: ...
    def _route_error(self, generation: int, prompt_id: str) -> None: ...
    def _spawn(self, fn: Callable[[], None], name: str) -> None: ...
    def on_button(self, button: str, kind: str) -> None: ...
    def _on_power(self, voltage: float) -> None: ...
    def _check_range_health(self, frame: SensorFrame) -> bool: ...
    def _on_terrain(self, classification: str, features: VibrationFeatures,
                    timestamp: float | None = None) -> None: ...
    def _start_stereo_scan(self) -> bool: ...
    def _on_scan(self, result: ScanResult, generation: int | None = None,
                 timestamp: float | None = None) -> None: ...
    def log_files(self) -> list[Path]: ...
