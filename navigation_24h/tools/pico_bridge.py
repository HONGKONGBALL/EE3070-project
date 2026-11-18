#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2.12,<3", "pyserial>=3.5,<4", "typer>=0.16,<1"]
# ///
# ─── How to run ───
# uv run --project navigation_24h navigation_24h/tools/pico_bridge.py
# ──────────────────
"""Independent Pico USB button confirmation against current navigation context."""

from __future__ import annotations

import time
from typing import ClassVar, Final, Literal, assert_never
from uuid import uuid4

import serial
import typer
from host_sim import CLOCK_REFRESH_MS, HostReply, HostSession
from project import Model, config
from pydantic import ConfigDict, Field, ValidationError

MAX_BUTTON_BOOTS: Final = 8


class ButtonEvent(Model):
    """One debounced physical press with boot and sequence identity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    type: Literal["button"]
    boot_id: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=1)
    stable_ms: int = Field(ge=40)
    pressed_at_ms: int = Field(ge=0)
    session_id: str
    generation: int = Field(ge=0)
    route_version: int = Field(ge=0)
    event_id: int = Field(ge=1)


class ButtonContext(Model):
    """Current navigation identity supplied to the physical button device."""

    type: str = "context"
    session_id: str
    generation: int
    route_version: int
    event_id: int
    ttl_ms: int


class Confirmation(Model):
    """Current action confirmation ready for the ESP32 command boundary."""

    type: str = "command"
    command: Literal["confirm_turn", "confirm_crossing", "confirm_arrival"]
    command_id: str
    session_id: str
    generation: int
    route_version: int
    event_id: int
    confirmation_source: str = "manual_confirm"


class Bridge:
    """Mutable physical-button deduplication and live navigation context."""

    def __init__(self) -> None:
        """Initialize fresh button and navigation sequence state."""
        self.host: HostSession = HostSession()
        self.presses: dict[str, int] = {}
        self.confirmed_event: tuple[str, int, int, int] | None = None

    def button_context(self, now_ms: int) -> ButtonContext | None:
        """Describe the currently valid action for the independent button device."""
        event = self.host.current_event
        if (
            event is None
            or event.type not in {"maneuver", "arrival_near"}
            or now_ms >= self.host.event_deadline
        ):
            return None
        return ButtonContext(
            session_id=event.session_id,
            generation=event.generation,
            route_version=event.route_version,
            event_id=event.event_id,
            ttl_ms=self.host.event_deadline - now_ms,
        )

    def receive_navigation(self, raw: bytes, now_ms: int) -> HostReply | None:
        """Parse one navigation USB input at the bridge boundary."""
        if not raw:
            return None
        try:
            return self.host.receive(raw.decode("utf-8"), now_ms)
        except (ValidationError, UnicodeDecodeError):
            print("NAVIGATION_FRAME_FORMAT")
            return None

    def confirmation(self, button: ButtonEvent, now_ms: int) -> Confirmation | None:
        """Correlate a unique physical press with a fresh current action."""
        previous = self.presses.get(button.boot_id, 0)
        if button.sequence <= previous:
            return None
        if len(self.presses) >= MAX_BUTTON_BOOTS and button.boot_id not in self.presses:
            self.presses.clear()
        self.presses[button.boot_id] = button.sequence
        event = self.host.current_event
        if event is None or now_ms >= self.host.event_deadline:
            return None
        context = (event.session_id, event.generation, event.route_version, event.event_id)
        button_context = (
            button.session_id,
            button.generation,
            button.route_version,
            button.event_id,
        )
        if context == self.confirmed_event or button_context != context:
            return None
        if (event.session_id, event.generation, event.route_version) != (
            self.host.gate.identity.session_id,
            self.host.gate.identity.generation,
            self.host.gate.identity.route_version,
        ):
            return None
        operation: Literal["confirm_turn", "confirm_crossing", "confirm_arrival"]
        match self.host.state:
            case "CROSSING_WAIT":
                operation = "confirm_crossing"
            case "ARRIVAL_NEAR":
                operation = "confirm_arrival"
            case "ALIGN_CONFIRM" | "TURN" | "VERIFY":
                operation = "confirm_turn"
            case (
                "IDLE"
                | "WAIT_FIX"
                | "ROUTING"
                | "FOLLOW"
                | "APPROACH"
                | "RELOCALIZE"
                | "REROUTING"
                | "PAUSED"
                | "ARRIVED"
            ):
                return None
            case unreachable:
                assert_never(unreachable)
        self.confirmed_event = context
        return Confirmation(
            command=operation,
            command_id=uuid4().hex,
            session_id=event.session_id,
            generation=event.generation,
            route_version=event.route_version,
            event_id=event.event_id,
        )


def main() -> None:
    """Bridge the two independently configured USB interfaces."""
    local = config()
    if not local.esp32.port or not local.pico.port or local.esp32.port == local.pico.port:
        raise typer.Exit(2)
    bridge = Bridge()
    with (
        serial.Serial(local.esp32.port, 115200, timeout=0.05) as navigation,
        serial.Serial(local.pico.port, 115200, timeout=0.05) as pico,
    ):
        _ = navigation.write(
            (bridge.host.status_request(time.monotonic_ns() // 1_000_000) + "\n").encode()
        )
        refresh_at = time.monotonic_ns() // 1_000_000 + CLOCK_REFRESH_MS
        while True:
            now_ms = time.monotonic_ns() // 1_000_000
            if now_ms >= refresh_at:
                _ = navigation.write((bridge.host.status_request(now_ms) + "\n").encode())
                refresh_at = now_ms + CLOCK_REFRESH_MS
            raw = navigation.read_until(b"\n", size=4097)
            reply = bridge.receive_navigation(raw, time.monotonic_ns() // 1_000_000)
            if reply is not None:
                _ = navigation.write((reply.model_dump_json() + "\n").encode())
                if (
                    reply.accepted
                    and (context := bridge.button_context(time.monotonic_ns() // 1_000_000))
                    is not None
                ):
                    _ = pico.write((context.model_dump_json() + "\n").encode())
            button_raw = pico.read_until(b"\n", size=1025)
            if not button_raw:
                continue
            try:
                button = ButtonEvent.model_validate_json(button_raw)
            except ValidationError:
                print("PICO_FRAME_FORMAT")
                continue
            confirmation = bridge.confirmation(button, time.monotonic_ns() // 1_000_000)
            if confirmation is not None:
                _ = navigation.write((confirmation.model_dump_json() + "\n").encode())
                print(confirmation.model_dump_json(), flush=True)


if __name__ == "__main__":
    typer.run(main)
