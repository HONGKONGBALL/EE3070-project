"""Bridge the diagnostic surface to the Mac host's production event loop."""

from __future__ import annotations

import platform
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, assert_never

from cane.app import CaneApp
from cane.app_types import ControlResult, JsonValue, SelectionResult, Status, TextResult
from cane.model import Priority
from cane.nav.demo import DEMO_DESTINATION, DEMO_ORIGIN, DEMO_TURN
from cane.pico_link import PowerEvent
from cane.terrain import fit_ground_plane, slope_from_plane

from .devices import serial_candidates
from .scenarios import PROFILES, ScenarioPicoLink

if TYPE_CHECKING:
    from .vision import MacVisionFeed, VisionStatus

class HostInfo(TypedDict):
    platform: str
    python: str
    mode: str


class SerialInfo(TypedDict):
    port: str | None
    candidates: list[str]
    protocol: str
    connected: bool
    controls_available: bool


class AudioInfo(TypedDict):
    backend: str
    error: str
    last_completed: str | None
    local_clips: int


class SimulationInfo(TypedDict):
    scenario: str | None
    coordinate_system: str
    last_error: str | None


class NoCameraInfo(TypedDict):
    enabled: bool
    ready: bool
    error: str


class MacStatus(Status):
    host: HostInfo
    serial: SerialInfo
    audio: AudioInfo
    simulation: SimulationInfo
    camera: NoCameraInfo | VisionStatus


class SimAction(StrEnum):
    ROUTE = "route"
    CROSSING = "crossing"
    ARRIVAL = "arrival"
    SLOPE_UP = "slope_up"
    SLOPE_DOWN = "slope_down"
    SLOPE_LEFT = "slope_left"
    SLOPE_RIGHT = "slope_right"


class MacController:
    """Mutable host adapters; all scenario mutations are queued on the main loop."""

    def __init__(self, app: CaneApp, link: ScenarioPicoLink | None = None) -> None:
        self.app, self.link = app, link
        self.last_error: str | None = None
        self.camera: MacVisionFeed | None = None
        self._ports = serial_candidates(pico_only=self.app.destination_only)
        self._demo_location: tuple[float, float] | None = None
        self._demo_fix_at = 0.0

    def status(self) -> MacStatus:
        snapshot = self.app.status()
        model = Path(self.app.cfg.voice.vosk_model_dir) / ("vosk-en" if self.app.lang == "en" else "vosk-cmn")
        reason = self.app._voice_unavailable_reason()
        snapshot["voice"] = {**snapshot["voice"], "backend": self.app.cfg.voice.backend,
                             "model_available": model.is_dir(),
                             "available": reason is None, "reason": reason,
                             "listening": self.app.listening, "text_input_available": True,
                             "cantonese_model": False}
        voice_bench = self.app.destination_only
        mode = "voice" if voice_bench else "simulation" if self.app.sim else "hardware"
        voice_port = getattr(self.app.pico, "port", None) if voice_bench else None
        return {
            **snapshot,
            "host": {"platform": platform.system(), "python": platform.python_version(), "mode": mode},
            "serial": {"port": voice_port if voice_bench else None if self.app.sim else self.app.cfg.pico.port,
                       "candidates": ([voice_port] if voice_port else []) if voice_bench else self._ports,
                       "protocol": getattr(self.app.pico, "protocol_name", "idle" if voice_bench else "simulation"),
                       "connected": (getattr(self.app.pico, "connected", False) if voice_bench
                                     else snapshot["pico_ok"] and not self.app.sim),
                       "controls_available": bool(getattr(self.app.pico, "controls_available", False))},
            "audio": {"backend": "afplay" if hasattr(self.app.audio, "error") else "console",
                      "error": getattr(self.app.audio, "error", ""),
                      "last_completed": getattr(self.app.audio, "last_completed", None),
                      "local_clips": len(list(Path(self.app.cfg.audio.dir).glob("*/*.wav")))},
            "simulation": {"scenario": self.link.scenario if self.link else None,
                           "coordinate_system": "synthetic" if self.app.sim else "phone",
                           "last_error": self.last_error},
            "camera": self.camera.status() if self.camera else
                      {"enabled": False, "ready": False, "error": "未启用摄像头；可用 --camera 显式启动"},
        }

    def log_files(self) -> list[Path]:
        return self.app.log_files()

    def on_location(self, lng: float, lat: float, accuracy: float) -> None:
        self.app.on_location(lng, lat, accuracy)

    def on_destination_text(self, text: str) -> TextResult:
        return self.app.on_destination_text(text)

    def select_destination(self, search_id: str, candidate_id: str) -> SelectionResult:
        """Select a place from the current voice search."""
        return self.app.select_destination(search_id, candidate_id)

    def search_origin(self, text: str) -> TextResult:
        return self.app.search_origin(text)

    def select_origin(self, search_id: str, candidate_id: str) -> SelectionResult:
        return self.app.select_origin(search_id, candidate_id)

    def use_phone_origin(self) -> SelectionResult:
        return self.app.use_phone_origin()

    def enqueue_control(self, action: str) -> ControlResult:
        if self.app.destination_only and action not in {
            "voice_input", "voice_stop", "wake_enable", "wake_disable", "cancel_destination",
        }:
            return {"accepted": False, "action": action, "error": "voice_setup_action"}
        if (action == "light" and not self.app.sim
                and not getattr(self.app.pico, "controls_available", False)):
            return {"accepted": False, "action": action,
                    "error": "serial_receive_only_or_protocol_unknown"}
        return self.app.enqueue_control(action)

    def camera_frame(self) -> bytes | None:
        return self.camera.image() if self.camera else None

    def refresh_simulated_location(self, now: float) -> None:
        """A stationary synthetic phone keeps sending fixes during a demonstration."""
        if self.app.sim and self._demo_location is not None and now - self._demo_fix_at >= 1:
            self.app.on_location(*self._demo_location, 2.0)
            self._demo_fix_at = now

    def enqueue_scenario(self, name: str) -> Mapping[str, JsonValue]:
        """Synthetic observations are available only in explicit simulation mode."""
        if not self.app.sim or self.link is None:
            return {"accepted": False, "error": "真实模式拒绝模拟输入"}
        allowed = set(PROFILES) | {action.value for action in SimAction}
        if name not in allowed:
            return {"accepted": False, "error": "未知模拟场景"}
        self.app._jobs.put(lambda: self._apply_scenario(name))
        return {"accepted": True, "name": name}

    def _apply_scenario(self, name: str) -> None:
        if self.link is None:
            return
        self.last_error = None
        self.link.select_scenario(name if name in PROFILES else "clear")
        self.app.logbook.write("simulation", scenario=name, evidence="synthetic")
        self.app.pico.inbox.put(PowerEvent(self.app.clock(), self.link.profile.supply))
        if name in PROFILES:
            return
        action = SimAction(name)
        match action:
            case SimAction.ROUTE:
                self.app.stop_navigation()
                self._demo_location = DEMO_ORIGIN
                self.app._on_location(*DEMO_ORIGIN, 2.0, self.app.clock())
                self.app.on_destination_text("演示终点")
            case SimAction.CROSSING:
                if self.app.navigator.active:
                    self._demo_location = (DEMO_TURN[0], DEMO_TURN[1] - 3 / 111195)
                    self.app._on_location(DEMO_TURN[0], DEMO_TURN[1] - 3 / 111195, 2.0, self.app.clock())
                else:
                    self.last_error = "先选择演示路线并确认目的地"
            case SimAction.ARRIVAL:
                if self.app.navigator.waiting_crossing is not None:
                    self.last_error = "请先自行判断过街并按确认；演示也不会跳过此步骤"
                elif self.app.navigator.active:
                    self._demo_location = DEMO_DESTINATION
                    self.app._on_location(*DEMO_DESTINATION, 2.0, self.app.clock())
                else:
                    self.last_error = "尚无已启动的演示路线"
            case SimAction.SLOPE_UP | SimAction.SLOPE_DOWN | SimAction.SLOPE_LEFT | SimAction.SLOPE_RIGHT:
                self._simulate_slope(action)
            case unreachable:
                assert_never(unreachable)

    def _simulate_slope(self, name: SimAction) -> None:
        import math

        import numpy as np

        angle = math.tan(math.radians(8))
        x, z = np.meshgrid(np.linspace(-1, 1, 20), np.linspace(1, 3, 20))
        lateral, longitudinal = {
            SimAction.SLOPE_UP: (0, -angle), SimAction.SLOPE_DOWN: (0, angle),
            SimAction.SLOPE_LEFT: (-angle, 0), SimAction.SLOPE_RIGHT: (angle, 0),
        }[name]
        y = lateral * x + longitudinal * z
        plane = fit_ground_plane(np.column_stack((x.ravel(), y.ravel(), z.ravel())))
        prompt, uphill, lateral = slope_from_plane(plane)
        self.app.last_scan = {"uphill_deg": uphill, "left_down_deg": lateral,
                              "step_distance_m": None}
        self.app._last_scan_t = self.app.clock()
        self.app.logbook.write("simulated_scan", uphill=uphill, left_down=lateral,
                               residual=plane.residual_m, evidence="synthetic_point_cloud")
        if prompt:
            self.app.say(prompt, Priority.SAFETY)
