"""Navigation state, user pause and independently retained alignment gates.

PAUSED suppresses navigation only: the app continues sensor safety checks.
Fault recovery restores a user pause, and a paused crossing still requires its
own confirmation after resume. Resume always loses the previous alignment.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Callable, assert_never


class State(enum.Enum):
    BOOT = "BOOT"
    READY = "READY"
    WALKING = "WALKING"
    STOPPED = "STOPPED"
    CROSSING = "CROSSING"
    PAUSED = "PAUSED"
    FAULT = "FAULT"

    @property
    def stationary(self) -> bool:
        return self in (State.READY, State.STOPPED, State.CROSSING)


class FlagEvent(enum.StrEnum):
    ALIGNED = "aligned"
    ORIENTATION_LOST = "orientation_lost"
    PAUSE = "pause"
    RESUME = "resume"
    NAV_CANCEL = "nav_cancel"


_S = State
TRANSITIONS: dict[tuple[State, str], State] = {
    (_S.BOOT, "boot_done"): _S.READY,
    (_S.READY, "motion_start"): _S.WALKING,
    (_S.STOPPED, "motion_start"): _S.WALKING,
    (_S.WALKING, "motion_stop"): _S.STOPPED,
    (_S.READY, "crossing_detected"): _S.CROSSING,
    (_S.WALKING, "crossing_detected"): _S.CROSSING,
    (_S.STOPPED, "crossing_detected"): _S.CROSSING,
    (_S.CROSSING, "crossing_confirmed"): _S.STOPPED,
    (_S.CROSSING, "nav_cancel"): _S.READY,
    (_S.FAULT, "recovered"): _S.READY,
}
for _st in State:
    if _st is not State.FAULT:
        TRANSITIONS[(_st, "fault")] = State.FAULT
    if _st in (State.READY, State.WALKING, State.STOPPED, State.CROSSING):
        TRANSITIONS[(_st, "pause")] = State.PAUSED


@dataclass(frozen=True, slots=True)
class Transition:
    t: float
    event: str
    src: State
    dst: State


class CaneStateMachine:
    """Mutable event accumulator, called only by the application loop."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.state = State.BOOT
        self.aligned = False
        self.paused = False
        self.test_mode = False
        self._paused_state = State.STOPPED
        self._fault_state = State.READY
        self.entered_at = clock()
        self.log: list[Transition] = []
        self._listeners: list[Callable[[Transition], None]] = []

    def on_transition(self, fn: Callable[[Transition], None]) -> None:
        self._listeners.append(fn)

    def time_in_state(self) -> float:
        return self._clock() - self.entered_at

    def handle(self, event: str) -> bool:
        """Apply a valid event; physical resume conditions are checked by app."""
        try:
            special = FlagEvent(event)
        except ValueError:
            special = None
        if special is not None:
            result = self._flag_event(special)
            if result is not None:
                return result
        dst = TRANSITIONS.get((self.state, event))
        if dst is None:
            return False
        if event == "fault":
            self._fault_state = self.state
        if event == "recovered":
            if self.paused:
                dst = State.PAUSED
            elif self._fault_state is State.CROSSING:
                dst = State.CROSSING
        return self._transition(event, dst)

    def _flag_event(self, event: FlagEvent) -> bool | None:
        """Return None when the transition table must also process the event."""
        match event:
            case FlagEvent.ALIGNED:
                if self.state.stationary and self.state is not State.CROSSING and not self.aligned:
                    self.aligned = True
                    return True
                return False
            case FlagEvent.ORIENTATION_LOST:
                changed, self.aligned = self.aligned, False
                return changed
            case FlagEvent.PAUSE:
                if not self.paused:
                    self._paused_state = self.state
                self.paused = True
                self.aligned = False
            case FlagEvent.RESUME:
                if self.state is not State.PAUSED:
                    return False
                self.paused = False
                dst = State.CROSSING if self._paused_state is State.CROSSING else State.STOPPED
                return self._transition(event.value, dst)
            case FlagEvent.NAV_CANCEL:
                self._paused_state = State.STOPPED
            case unreachable:
                assert_never(unreachable)
        return None

    def _transition(self, event: str, dst: State) -> bool:
        tr = Transition(self._clock(), event, self.state, dst)
        self.state, self.entered_at = dst, tr.t
        if event != "boot_done":
            self.aligned = False if dst in (
                State.CROSSING, State.FAULT, State.READY, State.PAUSED, State.STOPPED
            ) else self.aligned
        self.log.append(tr)
        for fn in self._listeners:
            fn(tr)
        return True

    @property
    def nav_allowed(self) -> bool:
        return not self.paused and self.state in (State.READY, State.WALKING, State.STOPPED)

    @property
    def turns_allowed(self) -> bool:
        return self.nav_allowed and self.aligned
