"""Single-owner serial loop and continuously persisted pairing evidence."""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

import serial
from pairing_models import ControlRequest, PairingAction, PairingError, PairingStatus
from pairing_serial import PairingSerial
from pairing_web_capture import RecordedSerial, Recorder, credential_values
from project import digest, now, redact, save

if TYPE_CHECKING:
    from pathlib import Path
    from threading import Event

    from pairing_web_state import WebController


def serial_owner(port: str, folder: Path, state: WebController, stop: Event) -> None:
    """Own all serial operations and evidence mutation on one worker thread."""
    recorder = Recorder()
    values = credential_values()
    started = now()
    reason = "CAPTURING"

    def observe(status: PairingStatus) -> None:
        recorder.observe(status)
        state.observe(status)

    def persist() -> None:
        files = recorder.persist(folder, values)
        view = state.view()
        save(
            folder / "result.json",
            {
                "scope": "usb_local_web_pairing",
                "started_at": started,
                "updated_at": now(),
                "reason": reason,
                "authorized": view.authorized,
                "operator_confirmation": "explicit_current_browser_context",
                "pairing_states": [item.state.value for item in recorder.statuses],
                "files": [{"path": str(path), "sha256": digest(path)} for path in files],
            },
        )

    try:
        connection = serial.Serial(
            port=None, baudrate=115200, timeout=0.05, write_timeout=2, exclusive=True
        )
        connection.dtr = False
        connection.rts = False
        connection.port = port
        with connection:
            session = PairingSerial(RecordedSerial(connection, recorder), observe)
            session.send(ControlRequest(action=PairingAction.STATUS))
            last_saved = monotonic()
            while not stop.is_set():
                state.dispatch(session.send)
                try:
                    _ = session.receive(monotonic() + 0.1)
                except PairingError as error:
                    if error.code != "SERIAL_TIMEOUT":
                        raise
                if monotonic() - last_saved >= 1:
                    persist()
                    last_saved = monotonic()
            reason = "CAPTURE_FINISHED"
    except (PairingError, serial.SerialException, OSError) as error:
        reason = redact(str(error))
        state.fail("请检查 USB 连接后重新启动确认服务")
    finally:
        persist()
