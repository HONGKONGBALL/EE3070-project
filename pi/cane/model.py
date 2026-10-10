"""Shared data types."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Priority(enum.IntEnum):
    """Lower value = more important. Arbiter always serves the lowest first."""

    CRITICAL = 0  # stop now, drop-off right ahead, system failure
    SAFETY = 1    # obstacle, head-level, step, rough ground
    SYSTEM = 2    # boot, battery, network, mode changes
    NAV = 3       # turns, crossing hand-off, arrival
    INFO = 4      # read-backs, confirmations

    @property
    def is_safety(self) -> bool:
        return self <= Priority.SAFETY


class Direction(enum.Enum):
    NONE = "none"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True)
class SensorFrame:
    """One sample set from the Pico. Distances in metres, None = invalid."""

    t: float
    front_m: float | None = None
    up_m: float | None = None
    tfmini_m: float | None = None
    tfmini_strength: int = 0
    pico_ms: int = 0


@dataclass(frozen=True)
class ButtonEvent:
    t: float
    button: str   # ok | dest | repeat | mode
    kind: str     # short | long


@dataclass
class OutputRequest:
    """A request to the single output arbiter.

    prompt_id names a pre-generated clip (see prompts.CATALOG). text is only
    used for live TTS route read-back (NAV/INFO, never safety).
    """

    prompt_id: str
    priority: Priority
    direction: Direction = Direction.NONE
    vibration: str | None = None
    text: str | None = None
    extra_clips: list[str] = field(default_factory=list)  # e.g. distance clip after a turn
    created: float = 0.0
    ttl_s: float = 5.0  # stale requests are dropped instead of played late

    @property
    def key(self) -> str:
        return "|".join([self.prompt_id, *self.extra_clips, self.text or ""])
