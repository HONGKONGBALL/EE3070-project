"""Parsed device observations and explicitly scoped metric distributions."""

from __future__ import annotations

from typing import ClassVar, Literal

from project import Model
from pydantic import AliasChoices, ConfigDict, Field


class Sample(Model):
    """One finite observation with device, source and capture clock identities."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="ignore",
        allow_inf_nan=False,
        strict=True,
    )
    monotonic_ms: int = Field(
        ge=0, validation_alias=AliasChoices("monotonic_ms", "device_monotonic_ms")
    )
    type: str = Field(min_length=1, max_length=64)
    phone_session_id: str | None = Field(default=None, min_length=1, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    fix_seq: int | None = Field(default=None, ge=0, le=2**53 - 1)
    source_timestamp_ms: int | None = Field(default=None, ge=0, le=2**53 - 1)
    fix_age_ms: float | None = Field(default=None, ge=0)
    source_age_ms: float | None = Field(default=None, ge=0)
    ble_latency_ms: float | None = Field(default=None, ge=0)
    ble_latency_kind: Literal["exact", "upper_bound"] | None = None
    ble_latency_upper_bound_ms: float | None = Field(default=None, ge=0)
    transport_age_upper_ms: float | None = Field(default=None, ge=0)
    processing_ms: float | None = Field(default=None, ge=0)
    ack_ms: float | None = Field(default=None, ge=0)
    free_internal: int | None = Field(default=None, ge=0)
    largest_internal: int | None = Field(default=None, ge=0)
    free_psram: int | None = Field(default=None, ge=0)
    main_stack_free: int | None = Field(default=None, ge=0)
    boot_id: str | None = Field(default=None, min_length=1, max_length=64)
    host_monotonic_ms: int | None = Field(default=None, ge=0)
    segment: str | None = Field(default=None, max_length=64)
    accepted: bool | None = None
    reason: str = ""


class Distribution(Model):
    """Finite nearest-rank samples and their collection availability."""

    count: int = Field(ge=0)
    p50: float | None
    p95: float | None
    p99: float | None
    status: Literal["observed", "waiting_condition"]


class ClockEpoch(Model):
    """One observed device clock epoch and its uptime range."""

    boot_id: str | None
    first_monotonic_ms: int
    last_monotonic_ms: int
    duration_ms: int
    samples: int


class Timing(Model):
    """Elapsed-time clock provenance across device restarts."""

    duration_ms: int
    duration_basis: Literal["host_monotonic", "device_clock_epochs"]
    epochs: tuple[ClockEpoch, ...]
    clock_reversals: int


class Metrics(Model):
    """Independent location, explicitly timed latency and resource observations."""

    samples: int
    duration_ms: int
    duration_basis: Literal["host_monotonic", "device_clock_epochs"]
    fix_age_ms: Distribution
    source_age_ms: Distribution
    ble_latency_ms: Distribution
    ble_latency_upper_bound_ms: Distribution
    processing_ms: Distribution
    ack_ms: Distribution
    min_free_internal: int | None
    min_largest_internal: int | None
    min_free_psram: int | None
    min_main_stack_free: int | None
    boot_count: int
    clock_epochs: tuple[ClockEpoch, ...]
    clock_reversals: int
    independent_measurements: int
    duplicate_measurement_records: int
    unidentified_measurement_records: int
    unclassified_ble_latency_records: int
    fix_age_population: Literal["independent_fix_received", "independent_status"]
    ignored_input_records: int = 0
    segment: str | None = None
