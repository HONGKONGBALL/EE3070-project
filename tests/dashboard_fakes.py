"""In-memory diagnostics controller: queue recording without hardware effects."""

from collections.abc import Mapping
from pathlib import Path

from pydantic import JsonValue


class QueueController:
    """Mutable controller records requests and returns deterministic snapshots."""

    def __init__(self, sim: bool = True) -> None:
        self.sim: bool = sim
        self.controls: list[str] = []
        self.scenarios: list[str] = []
        self.texts: list[str] = []

    def status(self) -> Mapping[str, JsonValue]:
        return {"sim": self.sim, "state": "READY", "test_mode": True}

    def log_files(self) -> list[Path]:
        return []

    def on_location(self, lng: float, lat: float, accuracy: float) -> None:
        del lng, lat, accuracy

    def on_destination_text(self, text: str) -> Mapping[str, JsonValue]:
        self.texts.append(text)
        return {"matched": None, "queued": True}

    def enqueue_control(self, action: str) -> Mapping[str, JsonValue]:
        self.controls.append(action)
        return {"queued": True, "action": action}

    def enqueue_scenario(self, name: str) -> Mapping[str, JsonValue]:
        self.scenarios.append(name)
        return {"queued": True, "scenario": name}

    def camera_frame(self) -> bytes | None:
        return None


class PreviewController(QueueController):
    def __init__(self, frame: bytes | None = None) -> None:
        super().__init__()
        self.frame: bytes | None = frame

    def camera_frame(self) -> bytes | None:
        return self.frame
