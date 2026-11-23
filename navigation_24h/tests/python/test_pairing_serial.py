"""Real PTY transport checks for USB pairing framing and correlation."""

from __future__ import annotations

import os
import select
import tty
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING

import pytest
import serial
from pairing_models import (
    REQUEST_ADAPTER,
    ControlRequest,
    PairingAction,
    PairingError,
    PairingRequest,
    PairingState,
    PairingStatus,
    ProgressState,
    ProgressStatus,
)
from pairing_serial import PairingSerial

if TYPE_CHECKING:
    from collections.abc import Generator
    from io import FileIO


@dataclass(frozen=True, slots=True)
class FirmwarePTY:
    """Synthetic firmware endpoint on a real pseudo-terminal master."""

    stream: FileIO
    port: str

    def request(self) -> PairingRequest:
        """Read one bounded host command with an event-driven deadline."""
        frame = bytearray()
        deadline = monotonic() + 3
        while not frame.endswith(b"\n"):
            remaining = deadline - monotonic()
            if remaining <= 0 or not select.select([self.stream], [], [], remaining)[0]:
                msg = "TEST_REQUEST_TIMEOUT"
                raise PairingError(msg)
            frame.extend(os.read(self.stream.fileno(), 1))
        return REQUEST_ADAPTER.validate_json(frame)

    def emit(self, status: PairingStatus) -> None:
        """Publish one typed firmware state through the PTY."""
        _ = self.stream.write(status.model_dump_json(exclude_none=True).encode() + b"\n")


@contextmanager
def firmware_pty() -> Generator[FirmwarePTY]:
    """Own a raw PTY pair for one isolated transport scenario."""
    master_fd, slave_fd = os.openpty()
    _ = tty.setraw(slave_fd)
    port = os.ttyname(slave_fd)
    with (
        os.fdopen(master_fd, "r+b", buffering=0) as master,
        os.fdopen(slave_fd, "r+b", buffering=0),
    ):
        yield FirmwarePTY(master, port)


def progress(
    command_id: str,
    state: ProgressState = PairingState.ARMED,
    *,
    accepted: bool = True,
) -> ProgressStatus:
    """Create a typed current-window firmware observation."""
    return ProgressStatus(
        type="pairing_status",
        state=state,
        command_id=command_id,
        window_id="window",
        connection_epoch=7,
        deadline_ms=60000,
        reason="TEST_STATE",
        accepted=accepted,
    )


class ObservedSerial:
    """Real serial adapter that signals the first incomplete transport read."""

    def __init__(self, connection: serial.Serial, partial: Event) -> None:
        self.connection: serial.Serial = connection
        self.partial: Event = partial

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        chunk = self.connection.read_until(expected, size)
        if chunk and not chunk.endswith(expected):
            self.partial.set()
        return chunk

    def write(self, data: bytes, /) -> int | None:
        return self.connection.write(data)

    def flush(self) -> None:
        self.connection.flush()


def test_real_pty_ignores_wrong_command_id_and_navigation_records() -> None:
    # Given: a real serial connection with mixed firmware output.
    observed: list[PairingStatus] = []
    request = ControlRequest(action=PairingAction.STATUS, command_id="current")
    with firmware_pty() as peer, serial.Serial(peer.port, timeout=0.02) as connection:
        session = PairingSerial(connection, observed.append)
        with ThreadPoolExecutor(max_workers=1) as workers:
            # When: the correlated exchange receives another command's reply first.
            future = workers.submit(session.exchange, request, 2)
            assert peer.request().command_id == "current"
            _ = peer.stream.write(b'{"type":"status","state":"IDLE"}\n')
            peer.emit(progress("earlier"))
            peer.emit(progress("current"))
            result = future.result(timeout=3)
    # Then: only the matching command completes the exchange.
    assert result.command_id == "current"
    assert [item.command_id for item in observed] == ["earlier", "current"]


def test_real_pty_preserves_frame_across_read_until_timeout() -> None:
    # Given: a reply split across a confirmed serial read timeout.
    partial = Event()
    observed: list[PairingStatus] = []
    frame = progress("current").model_dump_json().encode() + b"\n"
    with firmware_pty() as peer, serial.Serial(peer.port, timeout=0.02) as connection:
        session = PairingSerial(ObservedSerial(connection, partial), observed.append)
        with ThreadPoolExecutor(max_workers=1) as workers:
            # When: firmware supplies the second chunk after the first read completes.
            future = workers.submit(session.receive, monotonic() + 2)
            _ = peer.stream.write(frame[:40])
            assert partial.wait(timeout=1)
            _ = peer.stream.write(frame[40:])
            result = future.result(timeout=3)
    # Then: the complete frame crosses the model boundary exactly once.
    assert result.command_id == "current"
    assert len(observed) == 1


def test_real_pty_preserves_maximum_payload_with_split_crlf_terminator() -> None:
    # Given: a maximum-size JSON payload with the CRLF terminator split across reads.
    partial = Event()
    encoded = progress("current").model_dump_json().encode()
    payload = encoded + b" " * (1024 - len(encoded))
    with firmware_pty() as peer, serial.Serial(peer.port, timeout=0.02) as connection:
        session = PairingSerial(ObservedSerial(connection, partial), lambda _status: None)
        with ThreadPoolExecutor(max_workers=1) as workers:
            # When: the pending CR is observed before its final LF arrives.
            future = workers.submit(session.receive, monotonic() + 2)
            _ = peer.stream.write(payload + b"\r")
            assert partial.wait(timeout=1)
            _ = peer.stream.write(b"\n")
            result = future.result(timeout=3)
    # Then: the original 1024-byte payload is accepted as one complete frame.
    assert result.command_id == "current"


def test_real_pty_reports_malformed_pairing_status() -> None:
    # Given: a complete comparison JSONL record with its number field pending.
    malformed = (
        b'{"type":"pairing_status","state":"COMPARING","command_id":"open",'
        b'"window_id":"window","connection_epoch":7,"deadline_ms":25000,'
        b'"reason":"COMPARE_NUMBER"}\n'
    )
    with firmware_pty() as peer, serial.Serial(peer.port, timeout=0.02) as connection:
        session = PairingSerial(connection, lambda _status: None)
        _ = peer.stream.write(malformed)
        # When / Then: the live serial boundary reports the malformed pairing event.
        with pytest.raises(PairingError, match="MALFORMED_PAIRING_STATUS"):
            _ = session.receive(monotonic() + 1)


def test_real_pty_bounds_output_frame_capacity() -> None:
    # Given: a firmware line beyond the USB output capacity.
    with firmware_pty() as peer, serial.Serial(peer.port, timeout=0.02) as connection:
        session = PairingSerial(connection, lambda _status: None)
        with ThreadPoolExecutor(max_workers=1) as workers:
            receive = workers.submit(session.receive, monotonic() + 1)
            _ = peer.stream.write(b"x" * 1025 + b"\n")
            # When / Then: the transport reports the capacity boundary.
            with pytest.raises(PairingError, match="STATUS_CAPACITY"):
                _ = receive.result(timeout=2)


def test_serial_timeout_retains_typed_reason_through_context_cleanup() -> None:
    # Given: an owned serial connection whose read deadline has arrived.
    def receive_expired() -> None:
        with firmware_pty() as peer, serial.Serial(peer.port, timeout=0.02) as connection:
            session = PairingSerial(connection, lambda _status: None)
            _ = session.receive(monotonic())

    # When: the transport reports the timeout through resource cleanup.
    with pytest.raises(PairingError, match="SERIAL_TIMEOUT"):
        receive_expired()
    # Then: the original typed failure reaches the caller.
