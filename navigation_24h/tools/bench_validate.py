#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2.12,<3", "typer>=0.16,<1", "pyserial>=3.5,<4"]
# ///
# ─── How to run ───
# uv run --project navigation_24h navigation_24h/tools/bench_validate.py CAPTURE.jsonl
# ──────────────────
"""Validate startup continuity, USB status correlation and observed resource margins."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path  # noqa: TC003 - Typer resolves command parameter types at runtime.
from typing import ClassVar, Final, Literal, assert_never

import typer
from capture import CaptureLine
from project import Model, digest, save
from pydantic import AliasChoices, ConfigDict, Field, ValidationError

ACK_TARGET_MS: Final = 500
MAIN_STACK_MARGIN_BYTES: Final = 1024


class Payload(Model):
    """Finite device observations at the captured JSON boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="ignore", strict=True, allow_inf_nan=False
    )
    type: str
    monotonic_ms: int | None = Field(
        default=None, ge=0, validation_alias=AliasChoices("monotonic_ms", "device_monotonic_ms")
    )
    state: str = ""
    category: str = ""
    reason: str = ""
    boot_id: str | None = None
    build_id: str | None = None
    command_id: str = ""
    accepted: bool | None = None
    ack_ms: float | None = Field(default=None, ge=0)
    free_internal: int | None = Field(default=None, ge=0)
    largest_internal: int | None = Field(default=None, ge=0)
    free_psram: int | None = Field(default=None, ge=0)
    main_stack_free: int | None = Field(default=None, ge=0)
    reset_reason: int | None = Field(default=None, ge=0)


class ResourceCheck(Model):
    """Observed minimum and its explicit startup comparison threshold."""

    observed_bytes: int | None
    minimum_bytes: int
    status: Literal["satisfied", "attention", "waiting_condition"]


class BenchReport(Model):
    """Measured startup and USB status evidence for one completed capture."""

    scope: Literal["bench_startup_usb_status"] = "bench_startup_usb_status"
    source: str
    source_sha256: str
    capture_complete: bool
    outcome: Literal["ready", "attention", "waiting_condition"]
    records: int
    parsed_records: int
    malformed_capture_records: int
    malformed_device_records: int
    startup_ready: bool
    states: tuple[str, ...]
    idle_status_records: int
    boot_ids: tuple[str, ...]
    build_ids: tuple[str, ...]
    reset_reasons: tuple[int, ...]
    raw_reset_markers: int
    raw_boot_markers: int
    stack_overflow_markers: int
    panic_markers: int
    uptime_reversals: int
    uptime_first_ms: int | None
    uptime_last_ms: int | None
    capture_duration_ms: int
    expected_command_ids: tuple[str, ...]
    acknowledged_command_ids: tuple[str, ...]
    matched_command_ids: tuple[str, ...]
    maximum_ack_ms: float | None
    ack_target_ms: int = ACK_TARGET_MS
    internal_free: ResourceCheck
    largest_internal: ResourceCheck
    psram_free: ResourceCheck
    main_stack: ResourceCheck


def resource(value: int | None, minimum: int) -> ResourceCheck:
    """Compare an observed resource minimum against its explicit byte threshold."""
    return ResourceCheck(
        observed_bytes=value,
        minimum_bytes=minimum,
        status="waiting_condition"
        if value is None
        else "satisfied"
        if value >= minimum
        else "attention",
    )


def validate(
    source: Path, *, expected_command_ids: tuple[str, ...] = (), expected_build_id: str = ""
) -> BenchReport:
    """Read a completed CaptureLine stream and validate the startup observation window."""
    captures: list[CaptureLine] = []
    payloads: list[Payload] = []
    malformed_capture = malformed_device = 0
    with source.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                captured = CaptureLine.model_validate_json(line)
            except ValidationError:
                malformed_capture += 1
                continue
            captures.append(captured)
            if captured.valid_json:
                try:
                    payloads.append(Payload.model_validate(captured.payload))
                except ValidationError:
                    malformed_device += 1
    resources = tuple(item for item in payloads if item.type == "resources")
    checks = (
        resource(
            min(
                (item.free_internal for item in resources if item.free_internal is not None),
                default=None,
            ),
            48 * 1024,
        ),
        resource(
            min(
                (item.largest_internal for item in resources if item.largest_internal is not None),
                default=None,
            ),
            16 * 1024,
        ),
        resource(
            min(
                (item.free_psram for item in resources if item.free_psram is not None), default=None
            ),
            1024 * 1024,
        ),
        resource(
            min(
                (item.main_stack_free for item in resources if item.main_stack_free is not None),
                default=None,
            ),
            MAIN_STACK_MARGIN_BYTES,
        ),
    )
    uptimes = tuple(
        item.monotonic_ms
        for item in payloads
        if item.type in {"status", "resources"} and item.monotonic_ms is not None
    )
    reversals = sum(later < earlier for earlier, later in pairwise(uptimes))
    boots = tuple(sorted({item.boot_id for item in resources if item.boot_id}))
    builds = tuple(sorted({item.build_id for item in payloads if item.build_id}))
    states = tuple(item.state for item in payloads if item.type == "status")
    ready = any(
        item.reason == "NAVIGATION_READY"
        and (item.type == "boot" or (item.type == "diagnostic" and item.category == "boot"))
        for item in payloads
    )
    overflows = sum("stack overflow" in item.raw.lower() for item in captures)
    panics = sum("Guru Meditation Error" in item.raw for item in captures)
    resets = sum(item.raw.lstrip().startswith("rst:0x") for item in captures)
    raw_boots = sum(item.raw.lstrip().startswith("ESP-ROM:") for item in captures)
    acks = tuple(item for item in payloads if item.type == "ack")
    matching = tuple(item for item in acks if item.command_id in expected_command_ids)
    matched = tuple(sorted({item.command_id for item in matching if item.accepted is True}))
    max_ack = max((item.ack_ms for item in matching if item.ack_ms is not None), default=None)
    complete = (source.parent / "capture_end.json").is_file()
    attention = bool(
        overflows
        or panics
        or reversals
        or len(boots) > 1
        or resets > 1
        or raw_boots > 1
        or malformed_capture
        or malformed_device
        or any(check.status == "attention" for check in checks)
        or any(item.accepted is False for item in matching)
        or (max_ack is not None and max_ack > ACK_TARGET_MS)
        or (expected_build_id and any(build != expected_build_id for build in builds))
    )
    waiting = bool(
        not complete
        or not ready
        or not states
        or states[-1] != "IDLE"
        or not boots
        or not expected_command_ids
        or set(matched) != set(expected_command_ids)
        or any(item.ack_ms is None for item in matching)
        or any(check.status == "waiting_condition" for check in checks)
        or (expected_build_id and not builds)
    )
    clocks = tuple(item.host_monotonic_ms for item in captures)
    return BenchReport(
        source=str(source.resolve()),
        source_sha256=digest(source),
        capture_complete=complete,
        outcome="attention" if attention else "waiting_condition" if waiting else "ready",
        records=len(captures) + malformed_capture,
        parsed_records=len(payloads),
        malformed_capture_records=malformed_capture,
        malformed_device_records=malformed_device,
        startup_ready=ready,
        states=states,
        idle_status_records=sum(state == "IDLE" for state in states),
        boot_ids=boots,
        build_ids=builds,
        reset_reasons=tuple(
            sorted({item.reset_reason for item in resources if item.reset_reason is not None})
        ),
        raw_reset_markers=resets,
        raw_boot_markers=raw_boots,
        stack_overflow_markers=overflows,
        panic_markers=panics,
        uptime_reversals=reversals,
        uptime_first_ms=uptimes[0] if uptimes else None,
        uptime_last_ms=uptimes[-1] if uptimes else None,
        capture_duration_ms=max(clocks, default=0) - min(clocks, default=0),
        expected_command_ids=expected_command_ids,
        acknowledged_command_ids=tuple(
            sorted({item.command_id for item in acks if item.command_id})
        ),
        matched_command_ids=matched,
        maximum_ack_ms=max_ack,
        internal_free=checks[0],
        largest_internal=checks[1],
        psram_free=checks[2],
        main_stack=checks[3],
    )


def main(
    source: Path,
    expected_command_id: str = "",
    expected_build_id: str = "",
    output: Path | None = None,
) -> None:
    """Validate a captured startup and save its typed bench report."""
    ids = tuple(item.strip() for item in expected_command_id.split(",") if item.strip())
    report = validate(source, expected_command_ids=ids, expected_build_id=expected_build_id)
    save(output or source.parent / "bench_validation.json", report)
    print(report.model_dump_json(indent=2))
    match report.outcome:
        case "ready":
            raise typer.Exit(0)
        case "attention":
            raise typer.Exit(1)
        case "waiting_condition":
            raise typer.Exit(2)
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    typer.run(main)
