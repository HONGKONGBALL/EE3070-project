#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2.12,<3", "pyserial>=3.5,<4", "typer>=0.16,<1"]
# ///
# ─── How to run ───
# uv run --project navigation_24h navigation_24h/tools/capture.py --help
# ──────────────────
"""Bounded real-serial capture with operator and schedule provenance."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Annotated, Final

import serial
import typer
from metrics import Sample, analyze
from progress_store import update
from project import ROOT, Model, config, now, redact, save
from pydantic import Field, JsonValue, TypeAdapter, ValidationError

JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
MAX_LINE: Final = 4096
FIELD_ROUTE_COUNT: Final = 3
FIELD_ROUNDS: Final = 2


class Segment(Model):
    """One scheduled operator scenario."""

    start_minute: float = Field(ge=0)
    end_minute: float = Field(gt=0)
    label: str
    cases: tuple[str, ...] = ()


class Schedule(Model):
    """Continuous device-test segment plan."""

    duration_minutes: int = Field(gt=0)
    segments: tuple[Segment, ...]


class CaptureLine(Model):
    """Timestamped serial observation and its parsed JSON boundary."""

    captured_at: str
    host_monotonic_ms: int
    segment: str
    raw: str
    raw_sha256: str
    valid_json: bool
    payload: JsonValue = None


class FieldRoute(Model):
    """Operator-verified route identity with walking endpoints."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    name: str = ""
    origin: str
    destination: str
    coord_frame: str = "WGS84"
    operator_confirmed: bool = False


class FieldRoutes(Model):
    """Prepared routes for repeated on-site collection."""

    schema_version: int = 1
    routes: tuple[FieldRoute, ...]


def resolve(path: str) -> Path:
    """Resolve paths relative to the project or its parent workspace."""
    value = Path(path)
    if value.is_absolute():
        return value
    workspace = ROOT.parent / value
    return workspace if workspace.is_file() else ROOT / value


def decode_line(raw: bytes, segment: str, host_ms: int) -> CaptureLine:
    """Keep malformed frames as observations with deterministic input identity."""
    text = redact(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
    payload: JsonValue = None
    valid = False
    if len(raw) <= MAX_LINE:
        try:
            payload = JSON.validate_json(text)
            valid = True
        except ValidationError:
            valid = False
    return CaptureLine(
        captured_at=now(),
        host_monotonic_ms=host_ms,
        segment=segment,
        raw=text,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        valid_json=valid,
        payload=payload,
    )


def metric(line: CaptureLine) -> Sample | None:
    """Parse available firmware metric records from the serial boundary."""
    if not line.valid_json:
        return None
    try:
        return Sample.model_validate(line.payload).model_copy(
            update={"host_monotonic_ms": line.host_monotonic_ms, "segment": line.segment}
        )
    except ValidationError:
        return None


def collect(minutes: int, segments: tuple[Segment, ...], destination: Path) -> int:
    """Capture configured hardware for the requested measured wall-time interval."""
    local = config()
    if minutes <= 0 or not local.esp32.port or not local.operator.name:
        save(
            destination / "readiness.json",
            {
                "observed_at": now(),
                "status": "waiting_condition",
                "next_action": "登记 ESP32 串口、设备操作者及采集时长",
            },
        )
        return 2
    destination.mkdir(parents=True, exist_ok=True)
    samples: list[Sample] = []
    count = 0
    malformed = 0
    begin = time.monotonic()
    save(
        destination / "start.json",
        {
            "started_at": now(),
            "operator": local.operator.name,
            "port": local.esp32.port,
            "minutes": minutes,
            "config_version": local.config_version,
        },
    )
    try:
        with (
            serial.Serial(local.esp32.port, 115200, timeout=1) as connection,
            (destination / "session.jsonl").open("w", encoding="utf-8", buffering=1) as stream,
        ):
            while time.monotonic() - begin < minutes * 60:
                elapsed = (time.monotonic() - begin) / 60
                label = next(
                    (
                        item.label
                        for item in segments
                        if item.start_minute <= elapsed < item.end_minute
                    ),
                    "continuous",
                )
                raw = connection.read_until(b"\n", size=MAX_LINE + 1)
                if not raw:
                    continue
                line = decode_line(raw, label, time.monotonic_ns() // 1_000_000)
                _ = stream.write(line.model_dump_json() + "\n")
                count += 1
                malformed += not line.valid_json
                value = metric(line)
                if value is not None:
                    samples.append(value)
    except (serial.SerialException, OSError) as error:
        save(
            destination / "interruption.json",
            {
                "observed_at": now(),
                "reason": redact(str(error)),
                "elapsed_ms": int((time.monotonic() - begin) * 1000),
                "records": count,
            },
        )
        return 2
    save(destination / "metrics.json", analyze(tuple(samples)))
    save(
        destination / "collection.json",
        {
            "finished_at": now(),
            "records": count,
            "malformed": malformed,
            "elapsed_ms": int((time.monotonic() - begin) * 1000),
            "operator_review": "pending",
        },
    )
    return 0 if count else 2


def soak(minutes: int, schedule: str) -> int:
    """Run the registered continuous segmented device collection."""
    plan = Schedule.model_validate_json(resolve(schedule).read_text())
    expected_start = 0.0
    for segment in plan.segments:
        if segment.start_minute != expected_start or segment.end_minute <= segment.start_minute:
            return 2
        expected_start = segment.end_minute
    if minutes != plan.duration_minutes or expected_start != minutes:
        return 2
    folder = ROOT / "evidence/T22" / time.strftime("%Y%m%d-%H%M%S")
    code = collect(minutes, plan.segments, folder)
    update(
        "T22",
        "awaiting_review" if code == 0 else "waiting_condition",
        80 if code == 0 else 10,
        (),
        (str(folder.relative_to(ROOT)),),
        "核对分段指标、版本、20 次规划取消及操作者记录",
    )
    return code


def field(routes: str, rounds: int) -> int:
    """Save one explicit capture and review checklist for every route round."""
    plan = FieldRoutes.model_validate_json(resolve(routes).read_text())
    local = config()
    ready = (
        len(plan.routes) == FIELD_ROUTE_COUNT
        and rounds == FIELD_ROUNDS
        and all(route.operator_confirmed for route in plan.routes)
    )
    ready = ready and bool(local.esp32.port and local.operator.name)
    rows: list[JsonValue] = [
        {
            "route_id": route.id,
            "round": repeat,
            "status": "planned",
            "origin": route.origin,
            "destination": route.destination,
            "checkpoints": ["路口", "连接点", "入口", "锁屏导航"],
        }
        for route in plan.routes
        for repeat in range(1, rounds + 1)
    ]
    save(
        ROOT / "evidence/T23/field_plan.json",
        {
            "observed_at": now(),
            "rounds": rows,
            "status": "ready" if ready else "waiting_condition",
            "operator": local.operator.name,
        },
    )
    update(
        "T23",
        "running" if ready else "waiting_condition",
        20 if ready else 5,
        (),
        ("evidence/T23/field_plan.json",),
        "逐轮运行 capture.py ROUTE_ID --round-number N 并复核关键点",
    )
    return 0 if ready else 2


def main(
    route_id: str,
    round_number: Annotated[int, typer.Option()] = 1,
    minutes: Annotated[int, typer.Option()] = 8,
) -> None:
    """Capture one prepared field-route round from the configured serial device."""
    plan = FieldRoutes.model_validate_json((ROOT / "config/field_routes.json").read_text())
    route = next(
        (item for item in plan.routes if item.id == route_id and item.operator_confirmed), None
    )
    if route is None or round_number not in (1, 2):
        raise typer.Exit(2)
    output = ROOT / "evidence/T23" / route_id / f"round{round_number}"
    save(output / "route.json", route)
    raise typer.Exit(collect(minutes, (), output))


if __name__ == "__main__":
    typer.run(main)
