"""Follow an AMap walking route from phone fixes and decide what to announce.

Pure logic: no network, no audio. The app turns NavOutput into arbiter
requests and state-machine events.

- Turns are announced at the `announce_m` distances before the end of a step
  (AMap's step action happens at the end of the step) as "turn + distance".
- A step that is a street crossing triggers the crossing hand-off when the user
  reaches its start: the cane says "crossing ahead, stop and judge it yourself,
  press OK", and waits. It never says it is safe to cross.
- Fixes worse than `max_accuracy_m` are ignored. Phone GPS on a small closed
  site is off by metres; turn prompts are only played once the user has
  stopped and faced ahead (enforced by the arbiter).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import NavCfg
from ..model import Direction
from ..prompts import distance_clip
from .amap import Route
from .coords import haversine_m, point_segment_m

LEFT = {"turn_left", "slight_left"}
RIGHT = {"turn_right", "slight_right"}


@dataclass
class NavOutput:
    kind: str  # prompt | crossing | arrived | reroute
    prompt_id: str
    extra_clips: list[str] = field(default_factory=list)
    direction: Direction = Direction.NONE
    text: str | None = None


@dataclass
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
    def __init__(self, cfg: NavCfg, max_accuracy_m: float = 30.0, offroute_fixes: int = 3) -> None:
        self.cfg = cfg
        self.max_accuracy_m = max_accuracy_m
        self.offroute_fixes = offroute_fixes
        self.route: Route | None = None
        self.dest: tuple[float, float] | None = None
        self._announced: set[tuple[int, float]] = set()
        self._crossed: set[int] = set()
        self._offroute_count = 0
        self.position: Position | None = None
        self.waiting_crossing: int | None = None

    @property
    def active(self) -> bool:
        return self.route is not None

    def start(self, route: Route, dest: tuple[float, float]) -> list[NavOutput]:
        self.route, self.dest = route, dest
        self._announced.clear()
        self._crossed.clear()
        self._offroute_count = 0
        self.waiting_crossing = None
        return [NavOutput("prompt", "nav_start")]

    def stop(self) -> None:
        self.route = None
        self.dest = None
        self.position = None
        self.waiting_crossing = None

    def confirm_crossing(self) -> list[NavOutput]:
        if self.waiting_crossing is None:
            return []
        self._crossed.add(self.waiting_crossing)
        self.waiting_crossing = None
        return [NavOutput("prompt", "crossing_confirmed")]

    # ------------------------------------------------------------- locate
    def locate(self, lng: float, lat: float) -> Position:
        assert self.route is not None
        best = (float("inf"), 0, 0, 0.0)  # dist, step, seg, t
        for si, step in enumerate(self.route.steps):
            pts = step.polyline
            for k in range(len(pts) - 1):
                d, t = point_segment_m((lng, lat), pts[k], pts[k + 1])
                if d < best[0]:
                    best = (d, si, k, t)
        d, si, k, t = best
        pts = self.route.steps[si].polyline
        if len(pts) < 2:
            return Position(si, d, 0.0)
        seg_len = haversine_m(*pts[k], *pts[k + 1])
        rest = seg_len * (1 - t)
        for j in range(k + 1, len(pts) - 1):
            rest += haversine_m(*pts[j], *pts[j + 1])
        return Position(si, d, rest)

    # ------------------------------------------------------------- update
    def update(self, lng: float, lat: float, accuracy_m: float = 10.0) -> list[NavOutput]:
        if self.route is None or self.dest is None or accuracy_m > self.max_accuracy_m:
            return []
        if self.waiting_crossing is not None:
            return []  # nothing until the user confirms the crossing

        if haversine_m(lng, lat, *self.dest) <= self.cfg.arrive_m:
            self.stop()
            return [NavOutput("arrived", "nav_arrived")]

        pos = self.locate(lng, lat)
        self.position = pos
        if pos.off_route_m > self.cfg.offroute_m:
            self._offroute_count += 1
            if self._offroute_count >= self.offroute_fixes:
                self._offroute_count = 0
                return [NavOutput("reroute", "nav_offroute")]
            return []
        self._offroute_count = 0

        steps = self.route.steps
        # crossing hand-off: we are on a crossing step (or at the end of the step before it)
        nxt = pos.step + 1
        near_end = pos.to_step_end_m <= min(self.cfg.announce_m)
        for idx in (pos.step, nxt if near_end else None):
            if idx is not None and idx < len(steps) and steps[idx].is_crossing and idx not in self._crossed:
                self.waiting_crossing = idx
                return [NavOutput("crossing", "crossing_ahead", text=self._text(steps[idx]))]

        if pos.step >= len(steps) - 1:
            return []  # last step: only arrival matters
        step = steps[pos.step]
        pid = step.maneuver
        if pid is None:
            return []
        due = [th for th in sorted(self.cfg.announce_m) if pos.to_step_end_m <= th]
        if not due:
            return []
        th = due[0]
        if (pos.step, th) in self._announced:
            return []
        for t in due:  # don't replay the larger thresholds we skipped past
            self._announced.add((pos.step, t))
        extra = [distance_clip(pos.to_step_end_m)] if pos.to_step_end_m >= 5 else []
        return [NavOutput("prompt", pid, extra, direction_of(pid), self._text(step))]

    def _text(self, step) -> str | None:
        return step.instruction if self.cfg.read_route_with_tts else None
