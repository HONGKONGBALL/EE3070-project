"""Pure walking-route guidance with revocable, versioned directions.

Crossing confirmation only acknowledges the user's decision. It never means
that a road is safe. The app must invalidate its queued audio on version change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final, Literal

from ..config import NavCfg
from ..model import Direction
from ..prompts import distance_clip
from .amap import AmapError, Route, Step
from .coords import haversine_m, point_segment_m

LEFT: Final = {"turn_left", "slight_left"}
RIGHT: Final = {"turn_right", "slight_right"}
NavKind = Literal["prompt", "crossing", "arrived", "reroute"]


@dataclass(frozen=True, slots=True)
class NavOutput:
    kind: NavKind
    prompt_id: str
    extra_clips: list[str] = field(default_factory=list)
    direction: Direction = Direction.NONE
    text: str | None = None
    route_version: int = 0


@dataclass(frozen=True, slots=True)
class Position:
    step: int
    off_route_m: float
    to_step_end_m: float


def direction_of(prompt_id: str | None) -> Direction:
    if prompt_id in LEFT:
        return Direction.LEFT
    if prompt_id in RIGHT:
        return Direction.RIGHT
    return Direction.NONE


class Navigator:
    """Mutable navigation session; each replacement/revocation advances its version."""

    def __init__(self, cfg: NavCfg, max_accuracy_m: float = 30.0, offroute_fixes: int = 3) -> None:
        self.cfg: NavCfg = cfg
        self.max_accuracy_m: float = max_accuracy_m
        self.offroute_fixes: int = max(1, offroute_fixes)
        self.route: Route | None = None
        self.dest: tuple[float, float] | None = None
        self.route_version: int = 0
        self._announced: set[tuple[int, float]] = set()
        self._crossed: set[int] = set()
        self._offroute_count: int = 0
        self._suspended: bool = False
        self.position: Position | None = None
        self.waiting_crossing: int | None = None

    @property
    def active(self) -> bool:
        return self.route is not None

    def is_current(self, output: NavOutput) -> bool:
        """Directions can be replayed only while their route generation is live."""
        return self.active and not self._suspended and output.route_version == self.route_version

    def invalidate(self) -> None:
        """Revoke directions while retaining the destination for a route request."""
        self.route_version += 1
        self._suspended = True
        self.waiting_crossing = None
        self.position = None

    def start(self, route: Route, dest: tuple[float, float]) -> list[NavOutput]:
        if not route.steps or not any(step.polyline for step in route.steps):
            raise AmapError("walking route has no usable geometry")
        self.route_version += 1
        self.route, self.dest = route, dest
        self._announced.clear()
        self._crossed.clear()
        self._offroute_count = 0
        self._suspended = False
        self.waiting_crossing = None
        self.position = None
        return [self._output("prompt", "nav_start")]

    def stop(self) -> None:
        self.invalidate()
        self.route = None
        self.dest = None

    def confirm_crossing(self) -> list[NavOutput]:
        if self.waiting_crossing is None or self._suspended:
            return []
        self._crossed.add(self.waiting_crossing)
        self.waiting_crossing = None
        return [self._output("prompt", "crossing_confirmed")]

    def locate(self, lng: float, lat: float) -> Position:
        """Locate against segments or singleton points without assuming dense geometry."""
        if self.route is None:
            raise AmapError("no walking route is active")
        best = Position(0, float("inf"), 0.0)
        for si, step in enumerate(self.route.steps):
            pts = step.polyline
            if len(pts) == 1:
                distance = haversine_m(lng, lat, *pts[0])
                if distance < best.off_route_m:
                    best = Position(si, distance, 0.0)
            for k in range(len(pts) - 1):
                distance, fraction = point_segment_m((lng, lat), pts[k], pts[k + 1])
                if distance < best.off_route_m:
                    remaining = haversine_m(*pts[k], *pts[k + 1]) * (1 - fraction)
                    remaining += sum(haversine_m(*a, *b) for a, b in zip(pts[k + 1:], pts[k + 2:]))
                    best = Position(si, distance, remaining)
        return best

    def update(self, lng: float, lat: float, accuracy_m: float = 10.0) -> list[NavOutput]:
        if self.route is None or self.dest is None or self._suspended:
            return []
        if not all(math.isfinite(value) for value in (lng, lat, accuracy_m)) or not 0 <= accuracy_m <= self.max_accuracy_m:
            return []
        if self.waiting_crossing is not None:
            return []
        if haversine_m(lng, lat, *self.dest) <= self.cfg.arrive_m:
            self.stop()
            return [self._output("arrived", "nav_arrived")]
        pos = self.locate(lng, lat)
        if self.position is not None and pos.step != self.position.step:
            self.route_version += 1
        self.position = pos
        if pos.off_route_m > self.cfg.offroute_m:
            self._offroute_count += 1
            if self._offroute_count >= self.offroute_fixes:
                self._offroute_count = 0
                self.invalidate()
                return [self._output("reroute", "nav_offroute")]
            return []
        self._offroute_count = 0
        steps = self.route.steps
        thresholds = [value for value in self.cfg.announce_m if math.isfinite(value) and value > 0]
        near_end = pos.to_step_end_m <= min(thresholds, default=8.0)
        for idx in (pos.step, pos.step + 1 if near_end else None):
            if idx is not None and idx < len(steps) and steps[idx].is_crossing and idx not in self._crossed:
                # Any direction emitted before the handoff is now stale.
                self.route_version += 1
                self.waiting_crossing = idx
                return [self._output("crossing", "crossing_ahead", text=self._text(steps[idx]))]
        if pos.step >= len(steps) - 1:
            return []
        step = steps[pos.step]
        pid = step.maneuver
        if pid is None:
            return []
        due = [threshold for threshold in sorted(thresholds) if pos.to_step_end_m <= threshold]
        if not due or (pos.step, due[0]) in self._announced:
            return []
        self._announced.update((pos.step, threshold) for threshold in due)
        extra = [distance_clip(pos.to_step_end_m)] if pos.to_step_end_m >= 5 else []
        return [self._output("prompt", pid, extra, direction_of(pid), self._text(step))]

    def _output(self, kind: NavKind, prompt_id: str, extra: list[str] | None = None,
                direction: Direction = Direction.NONE, text: str | None = None) -> NavOutput:
        return NavOutput(kind, prompt_id, extra or [], direction, text, self.route_version)

    def _text(self, step: Step) -> str | None:
        return step.instruction if self.cfg.read_route_with_tts else None
