"""Top-level cane state machine.

    BOOT ──boot_done──► READY ──motion_start──► WALKING ──motion_stop──► STOPPED
                          ▲                        ▲  ◄────motion_start────┘
                          │                        │
     any ──fault──► FAULT ┘(recovered)   CROSSING ─┘ (crossing_confirmed -> STOPPED)

CROSSING (street-crossing hand-off) is entered from READY/WALKING/STOPPED when
the route reaches a crossing. The cane never decides whether it is safe to
cross: it waits for the user to press OK.

`aligned` tracks whether the user has stopped and faced ahead since the last
time orientation was lost. Left/right commands are only allowed when aligned.
Safety assessment runs in every state; the state only gates NAV behaviour and
which sensors run (stereo depth only while stationary).
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Callable


class State(enum.Enum):
    BOOT = "BOOT"
    READY = "READY"
    WALKING = "WALKING"
    STOPPED = "STOPPED"
    CROSSING = "CROSSING"
    FAULT = "FAULT"

    @property
    def stationary(self) -> bool:
        return self in (State.READY, State.STOPPED, State.CROSSING)


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

# events that change flags but not the state
FLAG_EVENTS = {"aligned", "orientation_lost"}


@dataclass
class Transition:
    t: float
    event: str
    src: State
    dst: State


class CaneStateMachine:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.state = State.BOOT
        self.aligned = False
        self.test_mode = False
        self.entered_at = clock()
        self.log: list[Transition] = []
        self._listeners: list[Callable[[Transition], None]] = []

    def on_transition(self, fn: Callable[[Transition], None]) -> None:
        self._listeners.append(fn)

    def time_in_state(self) -> float:
        return self._clock() - self.entered_at

    def handle(self, event: str) -> bool:
        """Apply an event. Returns True if the state or a flag changed."""
        if event == "aligned":
            if self.state.stationary and self.state is not State.CROSSING and not self.aligned:
                self.aligned = True
                return True
            return False
        if event == "orientation_lost":
            changed = self.aligned
            self.aligned = False
            return changed

        dst = TRANSITIONS.get((self.state, event))
        if dst is None:
            return False
        tr = Transition(self._clock(), event, self.state, dst)
        self.state = dst
        self.entered_at = tr.t
        if dst in (State.CROSSING, State.FAULT, State.READY) and event != "boot_done":
            # after a crossing or a fault, the user must re-align before turns
            self.aligned = False
        self.log.append(tr)
        for fn in self._listeners:
            fn(tr)
        return True

    @property
    def nav_allowed(self) -> bool:
        return self.state in (State.READY, State.WALKING, State.STOPPED)

    @property
    def turns_allowed(self) -> bool:
        return self.nav_allowed and self.aligned
