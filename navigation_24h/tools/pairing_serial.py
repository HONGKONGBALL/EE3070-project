"""Bounded USB JSONL framing and correlated pairing exchanges."""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING, ClassVar, Protocol

from pairing_models import (
    MAX_REQUEST_BYTES,
    MAX_STATUS_BYTES,
    STATUS_ADAPTER,
    PairingError,
    PairingRequest,
    PairingStatus,
)
from pydantic import BaseModel, ConfigDict, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable


class SerialConnection(Protocol):
    """Byte operations required from the serial adapter."""

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        """Return available bytes through a delimiter or timeout."""
        ...

    def write(self, data: bytes, /) -> int | None:
        """Write the supplied byte sequence."""
        ...

    def flush(self) -> None:
        """Complete pending serial output."""
        ...


class _Envelope(BaseModel):
    """Classify JSONL output at the transport boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="ignore")
    type: str

    def is_pairing_status(self) -> bool:
        return self.type == "pairing_status"


def decode_status(raw: bytes) -> PairingStatus | None:
    """Identify typed pairing replies in firmware JSONL output."""
    try:
        envelope = _Envelope.model_validate_json(raw)
    except ValidationError as error:
        if b'"pairing_status"' in raw:
            msg = "MALFORMED_PAIRING_STATUS"
            raise PairingError(msg) from error
        return None
    if not envelope.is_pairing_status():
        return None
    try:
        return STATUS_ADAPTER.validate_json(raw)
    except ValidationError as error:
        msg = "MALFORMED_PAIRING_STATUS"
        raise PairingError(msg) from error


class PairingSerial:
    """Mutable framing buffer shared by a single USB pairing session."""

    def __init__(
        self, connection: SerialConnection, observe: Callable[[PairingStatus], None]
    ) -> None:
        """Bind transport bytes and immediate typed-state observations."""
        self.connection: SerialConnection = connection
        self.observe: Callable[[PairingStatus], None] = observe
        self._buffer: bytearray = bytearray()

    def send(self, request: PairingRequest) -> None:
        """Send exactly one bounded JSON command with its line terminator."""
        payload = request.model_dump_json().encode()
        if len(payload) > MAX_REQUEST_BYTES:
            msg = "REQUEST_CAPACITY"
            raise PairingError(msg)
        framed = payload + b"\n"
        if self.connection.write(framed) != len(framed):
            msg = "SERIAL_WRITE"
            raise PairingError(msg)
        self.connection.flush()

    def receive(self, deadline: float) -> PairingStatus:
        """Accumulate timed-out read fragments until a complete status arrives."""
        while monotonic() < deadline:
            chunk = self.connection.read_until(b"\n", size=MAX_STATUS_BYTES + 2 - len(self._buffer))
            self._buffer.extend(chunk)
            complete = self._buffer.endswith(b"\n")
            content = bytes(self._buffer)
            if complete:
                content = content.removesuffix(b"\n")
            content = content.removesuffix(b"\r")
            if len(content) > MAX_STATUS_BYTES:
                msg = "STATUS_CAPACITY"
                raise PairingError(msg)
            if not complete:
                continue
            self._buffer.clear()
            status = decode_status(content)
            if status is not None:
                self.observe(status)
                return status
        msg = "SERIAL_TIMEOUT"
        raise PairingError(msg)

    def exchange(self, request: PairingRequest, timeout: float = 5) -> PairingStatus:
        """Await a command result carrying the exact transmitted command ID."""
        self.send(request)
        deadline = monotonic() + timeout
        while True:
            status = self.receive(deadline)
            if status.command_id == request.command_id and status.accepted is not None:
                return status
