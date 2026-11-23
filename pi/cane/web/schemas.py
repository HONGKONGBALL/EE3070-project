"""Closed request schemas for the diagnostics HTTP boundary."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

ControlAction = Literal[
    "confirm", "cancel_destination", "pause", "resume", "help", "repeat",
    "language", "light", "test_mode", "scan", "stop_navigation", "voice_input",
    "voice_stop", "wake_enable", "wake_disable",
]
ScenarioName = Literal[
    "clear", "obstacle", "head", "drop", "step_up", "rough", "sensor_lost",
    "low_power", "route", "crossing", "arrival", "slope_up", "slope_down",
    "slope_left", "slope_right",
]


class RequestModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)


class ControlRequest(RequestModel):
    action: ControlAction


class ScenarioRequest(RequestModel):
    name: ScenarioName


class ButtonPress(RequestModel):
    button: Literal["ok", "dest", "repeat", "mode", "vision"]
    kind: Literal["short", "long"] = "short"


class Fix(RequestModel):
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    accuracy: float = Field(default=50.0, ge=0, allow_inf_nan=False)
    token: str = Field(max_length=512)


class SpokenText(RequestModel):
    text: str = Field(min_length=1, max_length=200, pattern=r"\S")


class DestinationSelection(RequestModel):
    search_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    candidate_id: str = Field(min_length=1, max_length=128)
