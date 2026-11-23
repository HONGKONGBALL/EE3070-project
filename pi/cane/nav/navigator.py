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
from .coords import bearing_deg, haversine_m, point_segment_m

LEFT: Final = {"turn_left", "slight_left"}
RIGHT: Final = {"turn_right", "slight_right"}
NavKind = Literal["prompt", "crossing", "arrived", "reroute"]
# Travel shorter than this says nothing about the user's direction: it is within GPS error.
TRAVEL_M: Final = 12.0
# A fix matched to a part of the route this far along from the last one is another pass of the
# route close by, not a step the user took.
ROUTE_JUMP_M: Final = 40.0


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
    along_m: float = 0.0  # distance walked along the route from its start


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
        self._step_start_m: list[float] = []
        self.length_m: float = 0.0
        self._back_m: float = 0.0
        # The direction of the user's last stretch of travel, kept across a re-plan.
        self._travel_from: tuple[float, float] | None = None
        self.travel_bearing: float | None = None

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
        self._back_m = 0.0

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
        self._back_m = 0.0
        self._step_start_m, walked = [], 0.0
        for step in route.steps:
            self._step_start_m.append(walked)
            walked += sum(haversine_m(*a, *b) for a, b in zip(step.polyline, step.polyline[1:]))
        self.length_m = walked
        return [self._output("prompt", "nav_start")]

    def remaining_m(self) -> float | None:
        """Metres left along the route from the last fix. None before the first fix."""
        return None if self.position is None else max(0.0, self.length_m - self.position.along_m)

    def rerouted(self) -> list[NavOutput]:
        """What to say once a new route replaces the one the user left.

        Without this the user stands off the old route and hears nothing until 50 m before the
        next turn. Someone who walked past a turn is told to turn round. Otherwise the first
        instruction is given with its distance, unless the usual announcement is about to come.
        """
        if self.route is None or self._suspended:
            return []
        points = self.route.polyline
        ahead = next((point for point in points[1:] if haversine_m(*points[0], *point) >= 5.0), None)
        if self.travel_bearing is not None and ahead is not None:
            turn = abs((bearing_deg(*points[0], *ahead) - self.travel_bearing + 180) % 360 - 180)
            if turn > 120:
                return [self._output("prompt", "turn_around")]
        step = self.route.steps[0]
        length = self._step_start_m[1] if len(self._step_start_m) > 1 else 0.0
        thresholds = [value for value in self.cfg.announce_m if math.isfinite(value) and value > 0]
        if step.maneuver is None or len(self.route.steps) < 2 or length <= max(thresholds, default=0.0):
            return []
        return [self._output("prompt", step.maneuver, [distance_clip(length)], direction_of(step.maneuver),
                             self._text(step))]

    def stop(self) -> None:
        self.invalidate()
        self.route = None
        self.dest = None
        self._travel_from = self.travel_bearing = None

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
            start = self._step_start_m[si] if si < len(self._step_start_m) else 0.0
            if len(pts) == 1:
                distance = haversine_m(lng, lat, *pts[0])
                if distance < best.off_route_m:
                    best = Position(si, distance, 0.0, start)
            for k in range(len(pts) - 1):
                distance, fraction = point_segment_m((lng, lat), pts[k], pts[k + 1])
                if distance < best.off_route_m:
                    remaining = haversine_m(*pts[k], *pts[k + 1]) * (1 - fraction)
                    remaining += sum(haversine_m(*a, *b) for a, b in zip(pts[k + 1:], pts[k + 2:]))
                    length = sum(haversine_m(*a, *b) for a, b in zip(pts, pts[1:]))
                    best = Position(si, distance, remaining, start + length - remaining)
        return best

    def _travelled(self, lng: float, lat: float) -> None:
        """Remember the direction of the last stretch the user really covered."""
        if self._travel_from is None:
            self._travel_from = (lng, lat)
        elif haversine_m(*self._travel_from, lng, lat) >= TRAVEL_M:
            self.travel_bearing = bearing_deg(*self._travel_from, lng, lat)
            self._travel_from = (lng, lat)

    def _walking_back(self, previous: Position | None, pos: Position) -> bool:
        """True once the user has gone wrong_way_m back along the route.

        Distance from the route cannot show this: a user who sets off the wrong way down the right
        street stays on the route. Steps forward cancel steps back twice over, so GPS jitter while
        standing or walking on does not add up.
        """
        if previous is None:
            return False
        moved = pos.along_m - previous.along_m
        if abs(moved) > ROUTE_JUMP_M:
            self._back_m = 0.0
        elif moved < 0:
            self._back_m -= moved
        else:
            self._back_m = max(0.0, self._back_m - 2 * moved)
        if self._back_m < self.cfg.wrong_way_m:
            return False
        self._back_m = 0.0
        return True

    def update(self, lng: float, lat: float, accuracy_m: float = 10.0) -> list[NavOutput]:
        if self.route is None or self.dest is None or self._suspended:
            return []
        if not all(math.isfinite(value) for value in (lng, lat, accuracy_m)) or not 0 <= accuracy_m <= self.max_accuracy_m:
            return []
        self._travelled(lng, lat)
        if self.waiting_crossing is not None:
            return []
        if haversine_m(lng, lat, *self.dest) <= self.cfg.arrive_m:
            self.stop()
            return [self._output("arrived", "nav_arrived")]
        pos = self.locate(lng, lat)
        previous = self.position
        if previous is not None and pos.step != previous.step:
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
        if self._walking_back(previous, pos):
            return [self._output("prompt", "turn_around")]
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
