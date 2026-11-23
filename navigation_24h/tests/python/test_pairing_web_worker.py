"""Serial startup configures the selected port and line state before context entry."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING, Self

import pairing_web_worker
import serial
from pairing_models import REQUEST_ADAPTER, PairingAction
from pairing_web_state import WebController

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    import pytest


def test_serial_owner_configures_port_before_context_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a simulated serial context and isolated capture folder.
    events: list[str] = []
    writes: list[bytes] = []

    class ConfiguredSerial:
        """Mutable serial settings checked at the real context-entry boundary."""

        def __init__(
            self,
            *,
            port: str | None,
            baudrate: int,
            timeout: float,
            write_timeout: float,
            exclusive: bool,
        ) -> None:
            _ = baudrate, timeout, write_timeout, exclusive
            self.port: str | None = port
            self.dtr: bool = True
            self.rts: bool = True

        def __enter__(self) -> Self:
            assert self.port == "synthetic-port"
            assert (self.dtr, self.rts) == (False, False)
            events.append("entered")
            return self

        def __exit__(
            self,
            _kind: type[BaseException] | None,
            _error: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            events.append("closed")

        def write(self, data: bytes, /) -> int:
            writes.append(data)
            return len(data)

        def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
            _ = expected, size
            return b""

        def flush(self) -> None:
            return

    monkeypatch.setattr(serial, "Serial", ConfiguredSerial)
    monkeypatch.setattr(pairing_web_worker, "credential_values", lambda: ())
    stop = Event()
    stop.set()
    # When: the owner starts and cleanly finishes its synthetic session.
    pairing_web_worker.serial_owner("synthetic-port", tmp_path, WebController(), stop)
    # Then: context entry used the configured settings and the status frame was captured.
    assert events == ["entered", "closed"]
    assert REQUEST_ADAPTER.validate_json(writes[0]).action == PairingAction.STATUS
    assert (tmp_path / "tx.raw").read_bytes() == b"".join(writes)
