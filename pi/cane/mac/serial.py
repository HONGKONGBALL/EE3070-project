"""Exclusive USB serial transport; legacy distance streams are receive-only.

No commands are sent until a valid JSON message identifies that protocol. A
legacy stream locks this connection to input-only mode. TF_DATA raw distances
are centimetres by default; no sensor unit configuration is sent. Reconnection repeats
identification and never uploads firmware or falls back to simulation.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from collections.abc import Callable, Mapping
from typing import Literal, TypeAlias, TypedDict, TypeGuard, assert_never

import serial as pyserial

from cane.model import ButtonEvent, SensorFrame
from cane.pico_link import PicoLinkBase, PicoMessage, PicoWarning, PowerEvent

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
ProtocolName: TypeAlias = Literal["unknown", "json", "legacy"]
log = logging.getLogger(__name__)


class SerialStatus(TypedDict):
    port: str
    connected: bool
    protocol: ProtocolName
    error: str | None
    failures: int
    queued: int
    controls_available: bool


class InvalidSerialMessage(ValueError):
    """A non-finite or otherwise invalid JSON value was received."""


def _reject_constant(value: str) -> None:
    raise InvalidSerialMessage(f"invalid JSON constant: {value}")


def _numeric(value: JsonValue, lower: float, upper: float) -> TypeGuard[int | float]:
    match value:
        case bool():
            return False
        case int() | float():
            return lower <= value <= upper and math.isfinite(value)
        case None | str() | list() | dict():
            return False
        case unreachable:
            assert_never(unreachable)


def _json_message(line: bytes, now: float) -> PicoMessage | None:
    try:
        payload: JsonValue = json.loads(line, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    match payload:
        case {"t": "d", "f": front, "u": upper, "g": ground} as fields:
            strength, ms = fields.get("gs", 0), fields.get("ms", 0)
            if (_numeric(front, -1, 12000) and _numeric(upper, -1, 12000) and _numeric(ground, -1, 12000)
                    and type(strength) is int and 0 <= strength <= 65535
                    and type(ms) is int and 0 <= ms <= 0xFFFFFFFF):
                values = [None if value < 0 else value / 100 for value in (front, upper, ground)]
                return SensorFrame(now, values[0], values[1], values[2], strength, ms)
        case {"t": "b", "id": str(button), "ev": str(event)}:
            if button in {"ok", "dest", "repeat", "mode"} and event in {"short", "long"}:
                return ButtonEvent(now, button, event)
        case {"t": "p", "vsys": voltage}:
            if _numeric(voltage, 0, 20):
                return PowerEvent(now, float(voltage))
        case {"t": "w", "msg": str(message)}:
            if len(message) <= 160:
                return PicoWarning(now, message)
        case None | bool() | int() | float() | str() | list() | dict():
            return None
        case unreachable:
            assert_never(unreachable)
    return None


class MacSerialPicoLink(PicoLinkBase):
    """Mutable USB session, with bounded queues, reconnects and line buffering."""

    def __init__(self, port: str, baud: int = 115200, *, clock: Callable[[], float] = time.monotonic,
                 retry_s: float = 0.5, max_retries: int = 3, destination_button_only: bool = False) -> None:
        super().__init__()
        self.port, self.baud, self.clock = port, baud, clock
        self.protocol_name: ProtocolName = "unknown"
        self.connected = False
        self.destination_button_only = destination_button_only
        self.error: str | None = None
        self.inbox: queue.Queue[PicoMessage] = queue.Queue(maxsize=64)
        self._retry_s, self._max_retries, self._failures = max(0.01, retry_s), max(0, max_retries), 0
        self._ser: pyserial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop, self._opened, self._lock = threading.Event(), threading.Event(), threading.Lock()
        self._buffer = bytearray()
        self._dropping = False
        self._channels: list[tuple[float | None, float]] = [(None, float("-inf"))] * 3
        self._strength = 0
        self._legacy_values: tuple[float | None, ...] = (None, None, None)

    @property
    def controls_available(self) -> bool:
        """Output capability requires a live connection identified as JSON."""
        return (not self.destination_button_only and self.connected and self._ser is not None
                and self.protocol_name == "json" and not self._stop.is_set())

    def status(self) -> SerialStatus:
        return {"port": self.port, "connected": self.connected, "protocol": self.protocol_name,
                "error": self.error, "failures": self._failures, "queued": self.inbox.qsize(),
                "controls_available": self.controls_available}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._reader, daemon=True, name="mac-pico-rx")
        self._thread.start()

    def _disconnect(self) -> None:
        """Always close the old descriptor before attempting another connection."""
        with self._lock:
            old, self._ser = self._ser, None
            self.connected, self.protocol_name = False, "unknown"
            if old is not None:
                try:
                    old.close()
                except (pyserial.SerialException, OSError) as exc:
                    self.error = str(exc)
            self._buffer.clear()
            self._dropping = False
            self._channels = [(None, float("-inf"))] * 3
            self._legacy_values = (None, None, None)

    def _reader(self) -> None:
        while not self._stop.is_set():
            try:
                if self._ser is None:
                    opened = pyserial.Serial(self.port, self.baud, timeout=0.1, write_timeout=0.2, exclusive=True)
                    with self._lock:
                        if self._stop.is_set():
                            opened.close()
                            break
                        self._ser, self.connected = opened, True
                    self._opened.set()
                current = self._ser
                if current is None:
                    continue
                chunk = current.read(256)
                with self._lock:
                    if current is self._ser and not self._stop.is_set():
                        self._consume(chunk, self.clock())
                        self._expire_legacy(self.clock())
            except (pyserial.SerialException, OSError) as exc:
                self.error = str(exc)
                self._disconnect()
                self._failures += 1
                if self._failures > self._max_retries or self._stop.wait(self._retry_s):
                    break
        self._disconnect()

    def _put(self, message: PicoMessage) -> None:
        try:
            self.inbox.put_nowait(message)
        except queue.Full:
            try:
                _ = self.inbox.get_nowait()
            except queue.Empty:
                log.debug("serial queue drained concurrently")
            self.inbox.put_nowait(message)

    def _consume(self, chunk: bytes, now: float) -> None:
        for byte in chunk:
            if byte == 10:
                if not self._dropping:
                    self._line(bytes(self._buffer).rstrip(b"\r"), now)
                self._buffer.clear()
                self._dropping = False
            elif not self._dropping:
                if len(self._buffer) >= 512:
                    self._buffer.clear()
                    self._dropping = True
                else:
                    self._buffer.append(byte)

    def _line(self, line: bytes, now: float) -> None:
        if self.protocol_name != "legacy":
            message = _json_message(line, now)
            if message is not None:
                self.protocol_name, self.last_rx, self._failures, self.error = "json", now, 0, None
                if not self.destination_button_only or (
                    isinstance(message, ButtonEvent) and (message.button, message.kind) == ("dest", "short")
                ):
                    self._put(message)
                return
        if not self.destination_button_only and self.protocol_name != "json" and self._legacy(line, now):
            self.protocol_name, self.last_rx, self._failures, self.error = "legacy", now, 0, None
            self._emit_legacy(now)

    def _legacy(self, line: bytes, now: float) -> bool:
        try:
            fields = line.decode("ascii").split(",")
            if len(fields) != 5 or fields[0] not in {"TF_DATA", "US_DATA"}:
                return False
            tick, delta, first, second = (int(value) for value in fields[1:])
        except (ValueError, UnicodeDecodeError):
            return False
        if not (0 <= tick <= 0xFFFFFFFF and -1 <= delta <= 0xFFFFFFFF):
            return False
        if fields[0] == "TF_DATA":
            if not (-1 <= first <= 65535 and 0 <= second <= 65535):
                return False
            valid = 1 <= first <= 12000 and 100 <= second < 65535
            self._channels[2], self._strength = (first / 100 if valid else None, now), second if valid else 0
        else:
            if not all(-2 <= value <= 30000 for value in (first, second)):
                return False
            for index, value in enumerate((first, second)):
                self._channels[index] = (None if value < 0 else value * 0.000343 / 2, now)
        return True

    def _emit_legacy(self, now: float) -> None:
        values = tuple(value if 0 <= now - at <= 1.5 else None for value, at in self._channels)
        self._legacy_values = values
        strength = self._strength if values[2] is not None else 0
        self._put(SensorFrame(min(now, self.last_rx), values[0], values[1], values[2], strength))

    def _expire_legacy(self, now: float) -> None:
        if self.protocol_name == "legacy":
            values = tuple(value if 0 <= now - at <= 1.5 else None for value, at in self._channels)
            if values != self._legacy_values:
                self._emit_legacy(now)

    def send(self, msg: Mapping[str, JsonValue]) -> None:
        failed = False
        with self._lock:
            if self.destination_button_only or self._ser is None or self.protocol_name != "json" or self._stop.is_set():
                return
            if msg.get("t") not in {"h", "v", "l", "z"}:
                return
            try:
                payload = (json.dumps(dict(msg), allow_nan=False, separators=(",", ":")) + "\n").encode()
                if len(payload) > 512 or self._ser.write(payload) != len(payload):
                    raise pyserial.SerialException("incomplete or oversized serial command")
            except (pyserial.SerialException, OSError, ValueError) as exc:
                self.error, failed = str(exc), True
        if failed:
            self._disconnect()

    def close(self) -> None:
        self._stop.set()
        self._disconnect()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
