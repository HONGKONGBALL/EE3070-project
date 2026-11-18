"""Version-one position, command and output schema boundaries."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from project import Model
from pydantic import Field, TypeAdapter
from wire_outputs import AckFrame, EventFrame, StatusFrame


class Destination(Model):
    """User-confirmed destination in the phone coordinate frame."""

    name: str = Field(min_length=1, max_length=96)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    coord_frame: Literal["WGS84"] = "WGS84"


class Location(Model):
    """Independent timestamped location measurement."""

    type: Literal["location"] = "location"
    phone_session_id: str = Field(min_length=1, max_length=64)
    fix_seq: int = Field(ge=1, le=2**53 - 1)
    source_timestamp_ms: int = Field(ge=0, le=2**53 - 1)
    measurement_age_ms: int = Field(ge=0, le=2**32 - 1)
    device_sent_lower_bound_ms: int | None = Field(default=None, ge=0, le=2**53 - 1)
    coord_frame: Literal["WGS84"] = "WGS84"
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    horizontal_accuracy_m: float = Field(ge=0, allow_inf_nan=False)
    speed_mps: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    course_deg: float | None = Field(default=None, ge=0, lt=360, allow_inf_nan=False)


class Command(Model):
    """Session command with current route and event identity."""

    type: Literal["command"] = "command"
    command: Literal[
        "start",
        "cancel",
        "pause",
        "resume",
        "repeat",
        "confirm_turn",
        "confirm_crossing",
        "confirm_arrival",
        "get_status",
        "permission",
        "bind",
    ]
    command_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(max_length=64)
    generation: int = Field(ge=0, le=2**53 - 1)
    route_version: int = Field(default=0, ge=0, le=2**53 - 1)
    event_id: int = Field(default=0, ge=0, le=2**53 - 1)
    phone_session_id: str | None = Field(default=None, max_length=64)
    destination: Destination | None = None
    lease_ms: int = Field(default=3000, ge=0, le=3000)
    phone_send_monotonic_ms: int | None = Field(default=None, ge=0)
    confirmation_source: Literal["manual_confirm"] | None = None


Frame = Annotated[Location | Command, Field(discriminator="type")]
FRAME_ADAPTER = TypeAdapter(Frame)


def schema() -> str:
    """Return the frozen transport schema with protocol definitions."""
    schema_value = TypeAdapter(Frame | StatusFrame | EventFrame | AckFrame).json_schema()
    return json.dumps(schema_value, ensure_ascii=False, indent=2)
