"""Bounded raw-byte capture shared by the local pairing worker."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from time import monotonic_ns
from typing import TYPE_CHECKING, Final, Literal

from capture import CaptureLine, decode_line
from pairing_models import PairingError
from project import ROOT, Model, now
from secrets_config import KEYS, load

if TYPE_CHECKING:
    from pathlib import Path

    from pairing_models import PairingStatus
    from pairing_serial import SerialConnection

MAX_CAPTURE_BYTES: Final = 4 * 1024 * 1024


def credential_values() -> tuple[bytes, ...]:
    """Load local credential values at the worker boundary for evidence masking."""
    load(ROOT / "config/secrets.env")
    return tuple(value.encode() for key in KEYS if (value := os.environ.get(key, "")))


@dataclass(frozen=True, slots=True)
class Transfer:
    """One immutable serial call result before evidence redaction."""

    direction: Literal["read", "write"]
    captured_at: str
    host_monotonic_ms: int
    data: bytes
    write_result: int | None = None


class Fragment(Model):
    """Fragment reference into a credential-masked directional stream."""

    sequence: int
    direction: Literal["read", "write"]
    captured_at: str
    host_monotonic_ms: int
    offset: int
    bytes: int
    source_sha256: str
    data_base64: str
    write_result: int | None


def masked(data: bytes, values: tuple[bytes, ...]) -> bytes:
    """Mask known credentials across fragments while preserving byte offsets."""
    result = data
    for value in sorted(values, key=len, reverse=True):
        if value:
            result = result.replace(value, b"*" * len(value))
    return result


def captured_line(raw: bytes, safe: bytes, stamp: Transfer) -> CaptureLine:
    """Associate a full or final partial SDK line with its receive timestamp."""
    return decode_line(safe, "usb_pairing_web", stamp.host_monotonic_ms).model_copy(
        update={"captured_at": stamp.captured_at, "raw_sha256": hashlib.sha256(raw).hexdigest()}
    )


class Recorder:
    """Mutable byte and status history owned by the single serial worker."""

    def __init__(self) -> None:
        """Initialize ordered serial calls and typed state observations."""
        self.transfers: list[Transfer] = []
        self.statuses: list[PairingStatus] = []
        self.byte_count: int = 0

    def record(
        self, direction: Literal["read", "write"], data: bytes, written: int | None = None
    ) -> None:
        """Retain each read, timeout fragment and attempted write in call order."""
        if self.byte_count + len(data) > MAX_CAPTURE_BYTES:
            reason = "CAPTURE_CAPACITY"
            raise PairingError(reason)
        self.byte_count += len(data)
        self.transfers.append(
            Transfer(direction, now(), monotonic_ns() // 1_000_000, data, written)
        )

    def observe(self, status: PairingStatus) -> None:
        """Retain pairing state for the worker's separate UI publication."""
        self.statuses.append(status)

    def persist(self, folder: Path, values: tuple[bytes, ...]) -> tuple[Path, ...]:
        """Rewrite the current bounded snapshot for periodic and final evidence saves."""
        folder.mkdir(parents=True, exist_ok=True)
        received = b"".join(item.data for item in self.transfers if item.direction == "read")
        sent = b"".join(item.data for item in self.transfers if item.direction == "write")
        streams = {"read": masked(received, values), "write": masked(sent, values)}
        _ = (folder / "rx.raw").write_bytes(streams["read"])
        _ = (folder / "tx.raw").write_bytes(streams["write"])
        offsets = {"read": 0, "write": 0}
        line_start = 0
        last_read: Transfer | None = None
        with (
            (folder / "fragments.jsonl").open("w", encoding="utf-8") as fragments,
            (folder / "session.jsonl").open("w", encoding="utf-8") as lines,
        ):
            for sequence, item in enumerate(self.transfers):
                start = offsets[item.direction]
                end = start + len(item.data)
                safe = streams[item.direction][start:end]
                fragment = Fragment(
                    sequence=sequence,
                    direction=item.direction,
                    captured_at=item.captured_at,
                    host_monotonic_ms=item.host_monotonic_ms,
                    offset=start,
                    bytes=len(item.data),
                    source_sha256=hashlib.sha256(item.data).hexdigest(),
                    data_base64=base64.b64encode(safe).decode("ascii"),
                    write_result=item.write_result,
                )
                _ = fragments.write(fragment.model_dump_json() + "\n")
                offsets[item.direction] = end
                if item.direction != "read":
                    continue
                last_read = item
                while (newline := received.find(b"\n", line_start, end)) >= 0:
                    stop = newline + 1
                    line = captured_line(
                        received[line_start:stop], streams["read"][line_start:stop], item
                    )
                    _ = lines.write(line.model_dump_json() + "\n")
                    line_start = stop
            if last_read is not None and line_start < len(received):
                line = captured_line(received[line_start:], streams["read"][line_start:], last_read)
                _ = lines.write(line.model_dump_json() + "\n")
        return tuple(
            folder / name for name in ("rx.raw", "tx.raw", "fragments.jsonl", "session.jsonl")
        )


class RecordedSerial:
    """SerialConnection wrapper preserving transport bytes and return values."""

    def __init__(self, connection: SerialConnection, recorder: Recorder) -> None:
        """Bind the transport to its single worker's capture history."""
        self.connection: SerialConnection = connection
        self.recorder: Recorder = recorder

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        """Record every incoming fragment before pairing-specific classification."""
        data = self.connection.read_until(expected, size)
        self.recorder.record("read", data)
        return data

    def write(self, data: bytes, /) -> int | None:
        """Retain requested bytes and the underlying write result."""
        written = self.connection.write(data)
        self.recorder.record("write", data, written)
        return written

    def flush(self) -> None:
        """Delegate flushing to the owned transport."""
        self.connection.flush()
