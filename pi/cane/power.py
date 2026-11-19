"""Conservative VSYS supply-risk policy; thresholds need hardware calibration.

VSYS is a supply rail measurement. It does not reveal battery capacity,
state-of-charge, discharge current or remaining runtime. No host shutdown is
performed. Recovery needs 0.1 V of hysteresis and never resumes walking itself.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Final, TypedDict


class PowerStage(StrEnum):
    UNKNOWN = "unknown"
    NOMINAL = "nominal"
    CAUTION = "caution"
    REDUCED = "reduced"
    CRITICAL = "critical"


LEVELS: Final = (PowerStage.CRITICAL, PowerStage.REDUCED, PowerStage.CAUTION, PowerStage.NOMINAL)
THRESHOLDS: Final = (4.2, 4.5, 4.7)


class PowerStatus(TypedDict):
    stage: str
    vsys_v: float | None
    battery_percent: None
    remaining_minutes: None
    calibrated: bool
    source: str
    policy: str


class PowerPolicy:
    """Mutable hysteresis accumulator for supply measurements."""

    def __init__(self) -> None:
        self.stage = PowerStage.UNKNOWN
        self.voltage: float | None = None

    def update(self, voltage: float) -> bool:
        """Ignore physically invalid inputs; return whether the stage changed."""
        if not math.isfinite(voltage) or not 0.0 < voltage <= 6.0:
            return False
        self.voltage = voltage
        index = sum(voltage >= threshold for threshold in THRESHOLDS)
        candidate = LEVELS[index]
        if self.stage is not PowerStage.UNKNOWN:
            old_index = LEVELS.index(self.stage)
            if index > old_index and voltage < THRESHOLDS[old_index] + 0.1:
                return False
        changed = self.stage is not candidate
        self.stage = candidate
        return changed

    @property
    def reduced(self) -> bool:
        return self.stage in (PowerStage.REDUCED, PowerStage.CRITICAL)

    def status(self) -> PowerStatus:
        return {
            "stage": self.stage.value, "vsys_v": self.voltage,
            "battery_percent": None, "remaining_minutes": None,
            "calibrated": False, "source": "pico_vsys_supply_voltage",
            "policy": "uncalibrated supply thresholds; caution<4.7V, reduced<4.5V, critical<4.2V",
        }
