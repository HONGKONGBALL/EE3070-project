"""Device status, navigation event and acknowledgment schema values."""

from __future__ import annotations

from typing import Literal

from project import Model
from pydantic import Field


class StatusFrame(Model):
    """Current navigation identity, position quality and link state."""

    type: Literal["status"] = "status"
    session_id: str
    generation: int = Field(ge=0)
    route_version: int = Field(ge=0)
    state: Literal[
        "IDLE",
        "WAIT_FIX",
        "ROUTING",
        "FOLLOW",
        "APPROACH",
        "ALIGN_CONFIRM",
        "TURN",
        "VERIFY",
        "RELOCALIZE",
        "REROUTING",
        "CROSSING_WAIT",
        "PAUSED",
        "ARRIVAL_NEAR",
        "ARRIVED",
    ]
    quality: Literal["VALID", "DEGRADED", "LOST"]
    fix_seq: int = Field(ge=0)
    fix_age_ms: int = Field(ge=0)
    event_id: int = Field(ge=0)
    progress_m: float = Field(ge=0, allow_inf_nan=False)
    lateral_m: float = Field(ge=0, allow_inf_nan=False)
    segment: int = Field(ge=0)
    device_monotonic_ms: int = Field(ge=0)
    ble: str
    wifi: str
    mcp: str
    config_version: str


class EventFrame(Model):
    """Current versioned action with a device-clock deadline."""

    type: Literal[
        "route_ready", "maneuver", "relocalize", "rerouting", "arrival_near", "arrived", "fault"
    ]
    session_id: str
    generation: int = Field(ge=0)
    route_version: int = Field(ge=0)
    event_id: int = Field(ge=1)
    fix_seq: int = Field(ge=0)
    fix_age_ms: int = Field(ge=0)
    distance_m: float = Field(ge=0, allow_inf_nan=False)
    ttl_ms: int = Field(gt=0, le=2000)
    issued_at_ms: int = Field(ge=0)
    confirmation_source: Literal["", "manual_confirm", "valid_movement_evidence"]


class AckFrame(Model):
    """Correlated command or measurement acknowledgment."""

    type: Literal["ack"] = "ack"
    command_id: str
    session_id: str
    generation: int = Field(ge=0)
    accepted: bool
    reason: str
    device_monotonic_ms: int = Field(ge=0)
    ack_ms: int = Field(ge=0)
    phone_send_monotonic_ms: int | None = Field(default=None, ge=0)
    fix_seq: int | None = Field(default=None, ge=0)
