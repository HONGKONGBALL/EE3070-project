#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2.12,<3", "pyserial>=3.5,<4", "typer>=0.16,<1"]
# ///
# ─── How to run ───
# uv run --project navigation_24h navigation_24h/tools/host_sim.py --port /dev/cu.usbmodemDEVICE
# ──────────────────
"""USB or STDIO host event gate, clock exchange and correlated ACK output."""

from __future__ import annotations

import sys
import time
from typing import Annotated, ClassVar, Final, Literal
from uuid import uuid4

import serial
import typer
from host_gate import Event, Gate, Identity
from project import Model
from pydantic import ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
MAX_CLOCK_RTT_MS: Final = 2000
CLOCK_REFRESH_MS: Final = 5000
type NavigationState = Literal[
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


class Envelope(Model):
    """Dispatch fields from one JSONL input."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    type: str


class StatusFrame(Identity):
    """Current firmware identity and monotonic clock."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    device_monotonic_ms: int = Field(ge=0)
    state: NavigationState
    event_id: int = Field(default=0, ge=0)


class ClockAck(Model):
    """Echoed timing fields from a get_status request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    command_id: str
    phone_send_monotonic_ms: int = Field(ge=0)
    device_monotonic_ms: int = Field(ge=0)


class HostReply(Model):
    """Correlated host disposition for a firmware event."""

    type: str = "ack"
    session_id: str
    generation: int
    route_version: int
    event_id: int
    accepted: bool
    reason: str


class StatusRequest(Identity):
    """A typed get_status request with a monotonic timing echo."""

    type: str = "command"
    command: str = "get_status"
    command_id: str
    phone_send_monotonic_ms: int


class HostSession:
    """Mutable session clock, permission and event deduplication accumulator."""

    def __init__(self, *, integrated: bool = False) -> None:
        """Initialize one host link with a freshly synchronized clock."""
        self.gate: Gate = Gate(
            Identity(session_id="", generation=0, route_version=0), integrated=integrated
        )
        self.clock_offset_upper: int | None = None
        self.pending: dict[str, int] = {}
        self.current_event: Event | None = None
        self.event_deadline: int = 0
        self.state: NavigationState = "IDLE"

    def status_request(self, now_ms: int) -> str:
        """Create a bounded timing request for the current firmware link."""
        request_id = uuid4().hex
        self.pending = {request_id: now_ms}
        identity = self.gate.identity
        return StatusRequest(
            session_id=identity.session_id,
            generation=identity.generation,
            route_version=identity.route_version,
            command_id=request_id,
            phone_send_monotonic_ms=now_ms,
        ).model_dump_json()

    def receive(self, raw: str, now_ms: int) -> HostReply | None:
        """Parse an input and accept only a current, timed navigation event."""
        payload = JSON.validate_json(raw)
        kind = Envelope.model_validate(payload).type
        if kind == "status":
            status = StatusFrame.model_validate(payload)
            identity = Identity(
                session_id=status.session_id,
                generation=status.generation,
                route_version=status.route_version,
            )
            if identity != self.gate.identity:
                self.gate = Gate(identity, integrated=self.gate.integrated)
                self.current_event = None
            self.state = status.state
            return None
        if kind == "ack":
            try:
                ack = ClockAck.model_validate(payload)
            except ValidationError:
                return None
            sent = self.pending.pop(ack.command_id, None)
            if (
                sent == ack.phone_send_monotonic_ms
                and sent is not None
                and 0 <= now_ms - sent <= MAX_CLOCK_RTT_MS
            ):
                self.clock_offset_upper = ack.device_monotonic_ms - sent
            return None
        if kind not in {
            "maneuver",
            "arrival_near",
            "arrived",
            "route_ready",
            "rerouting",
            "relocalize",
            "fault",
        }:
            return None
        event = Event.model_validate(payload)
        if self.clock_offset_upper is None:
            accepted, reason = False, "TIMING_REFERENCE"
        else:
            transit_age = max(0, now_ms - event.issued_at_ms + self.clock_offset_upper)
            decision = self.gate.accept(
                event, received_at_ms=now_ms, now_ms=now_ms, transit_age_ms=transit_age
            )
            accepted, reason = decision.accepted, decision.reason
            if accepted:
                self.current_event = event
                self.event_deadline = now_ms + event.ttl_ms - transit_age
        return HostReply(
            session_id=event.session_id,
            generation=event.generation,
            route_version=event.route_version,
            event_id=event.event_id,
            accepted=accepted,
            reason=reason,
        )


def main(
    port: Annotated[str, typer.Option()] = "", integrated: Annotated[bool, typer.Option()] = False
) -> None:
    """Run the host interface over independent USB serial or JSONL STDIO."""
    session = HostSession(integrated=integrated)
    if port:
        with serial.Serial(port, 115200, timeout=1) as link:
            _ = link.write(
                (session.status_request(time.monotonic_ns() // 1_000_000) + "\n").encode()
            )
            refresh_at = time.monotonic_ns() // 1_000_000 + CLOCK_REFRESH_MS
            while True:
                now_ms = time.monotonic_ns() // 1_000_000
                if now_ms >= refresh_at:
                    _ = link.write((session.status_request(now_ms) + "\n").encode())
                    refresh_at = now_ms + CLOCK_REFRESH_MS
                raw = link.read_until(b"\n", size=4097)
                if not raw:
                    continue
                try:
                    reply = session.receive(raw.decode("utf-8"), time.monotonic_ns() // 1_000_000)
                except (ValidationError, UnicodeDecodeError):
                    print("FRAME_FORMAT", file=sys.stderr)
                    continue
                if reply is not None:
                    print(reply.model_dump_json(), flush=True)
                    _ = link.write((reply.model_dump_json() + "\n").encode())
    else:
        print(session.status_request(time.monotonic_ns() // 1_000_000), flush=True)
        for raw in sys.stdin:
            try:
                reply = session.receive(raw, time.monotonic_ns() // 1_000_000)
                if reply is not None:
                    print(reply.model_dump_json(), flush=True)
            except ValidationError:
                print("FRAME_FORMAT", file=sys.stderr)


if __name__ == "__main__":
    typer.run(main)
