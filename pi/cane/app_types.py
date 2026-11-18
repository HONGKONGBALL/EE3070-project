"""Small contracts shared by the host and its synchronous services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NotRequired, Protocol, TypeAlias, TypedDict

from .power import PowerStatus
from .config import Destination
from .voice.places import SearchView

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class ControlAction(StrEnum):
    CONFIRM = "confirm"
    CANCEL = "cancel_destination"
    PAUSE = "pause"
    RESUME = "resume"
    HELP = "help"
    REPEAT = "repeat"
    LANGUAGE = "language"
    LIGHT = "light"
    TEST_MODE = "test_mode"
    SCAN = "scan"
    STOP_NAVIGATION = "stop_navigation"
    VOICE_INPUT = "voice_input"
    VOICE_STOP = "voice_stop"
    WAKE_ENABLE = "wake_enable"
    WAKE_DISABLE = "wake_disable"


class ControlState(TypedDict):
    action: str
    executed: bool
    blocked_reason: str
    time: float


class ControlResult(TypedDict):
    accepted: bool
    action: str
    error: NotRequired[str]


@dataclass(frozen=True, slots=True)
class QueuedControl:
    action: ControlAction
    destination: Destination | None = None
    crossing: bool = False
    search_id: str = ""


class TextResult(TypedDict):
    matched: str | None
    name: NotRequired[str]
    action: NotRequired[str]
    accepted: NotRequired[bool]
    search_state: NotRequired[str]


class SelectionResult(TypedDict):
    accepted: bool
    name: NotRequired[str]
    error: NotRequired[str]


class ScanStatus(TypedDict):
    uphill_deg: float
    left_down_deg: float
    step_distance_m: float | None


class SearchOriginView(TypedDict):
    kind: Literal["iphone", "manual"]
    name: NotRequired[str]


class VoiceStatus(TypedDict):
    available: bool
    reason: str | None
    listening: bool
    text_input_available: bool
    cantonese_model: bool
    backend: NotRequired[str]
    model_available: NotRequired[bool]
    recording: NotRequired[bool]
    manual_stop: NotRequired[bool]
    can_stop_recording: NotRequired[bool]
    wake_enabled: NotRequired[bool]
    wake_phrase: NotRequired[str]
    wake_retrying: NotRequired[bool]
    place_search_online: NotRequired[bool]
    place_search_source: NotRequired[str]
    phone_location_ready: NotRequired[bool]
    search_origin: NotRequired[SearchOriginView | None]
    origin_search: NotRequired[SearchView]


class VoiceSearchStatus(VoiceStatus, SearchView):
    """Published voice availability and current destination choices."""


class Status(TypedDict):
    time: float
    state: str
    aligned: bool
    paused: bool
    help_requested: bool
    help_delivery: str
    test_mode: bool
    sim: bool
    language: str
    pico_ok: bool
    vsys: float | None
    tfmini_role: str
    sensors: JsonValue
    imu: JsonValue
    terrain: str
    hazards: list[str]
    fault_reasons: list[str]
    resume_blocked_reasons: list[str]
    safety_hold: bool
    orientation_ok: bool
    speaking: str | None
    queue: list[str]
    recent_outputs: JsonValue
    nav: JsonValue
    power: PowerStatus
    capabilities: JsonValue
    voice: VoiceSearchStatus
    last_control: ControlState | None
    control_pending: int
    scan: ScanStatus | None


class ImuSource(Protocol):
    """Observed cane motion; this does not claim full human body tracking."""

    @property
    def moving(self) -> bool: ...

    @property
    def pitch_deg(self) -> float | None: ...

    def window(self) -> list[float]: ...
