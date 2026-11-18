"""Deterministic simulated sensors, fed into the ordinary production pipeline."""

import math
import queue
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from cane.app_types import JsonValue
from cane.hazard import expected_ground_range
from cane.model import SensorFrame
from cane.pico_link import PicoLinkBase, PowerEvent

GROUND: Final = expected_ground_range(0.85, 25)


@dataclass(frozen=True, slots=True)
class ScenarioError(ValueError):
    name: str

    def __str__(self) -> str:
        return f"未知模拟场景：{self.name}"


@dataclass(frozen=True, slots=True)
class SensorProfile:
    front: float = 3.8
    head: float = 3.8
    ground: float = GROUND
    moving: bool = False
    disconnected: bool = False
    supply: float = 5.0


PROFILES: Final = {
    "clear": SensorProfile(),
    "obstacle": SensorProfile(front=0.4),
    "head": SensorProfile(head=0.7),
    "drop": SensorProfile(ground=GROUND + 0.4, moving=True),
    "step_up": SensorProfile(ground=GROUND - 0.4, moving=True),
    "rough": SensorProfile(moving=True),
    "sensor_lost": SensorProfile(disconnected=True),
    "low_power": SensorProfile(supply=4.1),
}


class ScenarioPicoLink(PicoLinkBase):
    """Own the current synthetic profile and publish bounded-rate fake readings."""

    def __init__(self, rate_hz: float = 20) -> None:
        super().__init__()
        self.rate_hz = rate_hz
        self.scenario = "clear"
        self.profile = PROFILES["clear"]
        self.sent: deque[Mapping[str, JsonValue]] = deque(maxlen=200)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def select_scenario(self, name: str) -> None:
        """Change only synthetic input; never issue a physical output command."""
        if name not in PROFILES:
            raise ScenarioError(name)
        with self._lock:
            self.scenario, self.profile = name, PROFILES[name]
        while True:
            try:
                self.inbox.get_nowait()
            except queue.Empty:
                break

    def sample(self, now: float) -> SensorFrame | None:
        """One fresh synthetic reading, or no reading during a disconnect."""
        with self._lock:
            profile = self.profile
        if profile.disconnected:
            return None
        return SensorFrame(now, profile.front, profile.head, profile.ground, 900, int(now * 1000))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="scenario-pico")
        self._thread.start()

    def _run(self) -> None:
        counter = 0
        while not self._stop.is_set():
            now = time.monotonic()
            frame = self.sample(now)
            if frame is not None:
                self.inbox.put(frame)
                self.last_rx = now
                if counter % 20 == 0:
                    self.inbox.put(PowerEvent(now, self.profile.supply))
            counter += 1
            self._stop.wait(1 / self.rate_hz)

    def send(self, msg: Mapping[str, JsonValue]) -> None:
        self.sent.append(msg)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


class SimImu:
    """Explicit synthetic motion, never substituted for a real IMU in hardware mode."""

    pitch_deg: float | None = None

    def __init__(self, link: ScenarioPicoLink) -> None:
        self.link = link

    @property
    def moving(self) -> bool:
        return self.link.profile.moving

    def window(self) -> list[float]:
        amplitude = 4.0 if self.link.scenario == "rough" else 0.6
        return [9.81 + amplitude * math.sin(2 * math.pi * 30 * i / 200) for i in range(200)]
