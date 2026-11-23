"""Interactive PTY approval and service-command dispatch checks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import cli
import environment
import pairing_cli
import pytest
import serial
import typer
from pairing_models import (
    ComparingStatus,
    DecisionRequest,
    PairingAction,
    PairingError,
    PairingRequest,
    PairingState,
)
from project import ROOT
from test_pairing_serial import FirmwarePTY, firmware_pty, progress
from typer.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

    from pairing_cli import PairingOptions


def simulate_pairing(
    peer: FirmwarePTY, *, approve: bool, late: bool = False
) -> tuple[PairingRequest, ...]:
    """Carry synthetic firmware through one explicit operator decision."""
    opened = peer.request()
    peer.emit(progress(opened.command_id))
    peer.emit(
        ComparingStatus(
            type="pairing_status",
            state=PairingState.COMPARING,
            command_id=opened.command_id,
            window_id="window",
            connection_epoch=7,
            number=42,
            deadline_ms=25000,
            reason="COMPARE_NUMBER",
        )
    )
    decision = peer.request()
    expected = PairingAction.CONFIRM if approve else PairingAction.REJECT
    assert isinstance(decision, DecisionRequest)
    assert decision.action == expected
    assert (decision.window_id, decision.connection_epoch, decision.number) == ("window", 7, 42)
    if late or not approve:
        peer.emit(progress(decision.command_id, PairingState.CLOSED, accepted=not late))
    else:
        peer.emit(progress(decision.command_id, PairingState.APPROVED))
        peer.emit(progress(decision.command_id, PairingState.AUTHORIZED))
    return opened, decision


@pytest.mark.parametrize(("answer", "approve", "exit_code"), [("yes\n", True, 0), ("\n", False, 2)])
def test_interactive_real_pty_sends_explicit_operator_decision(
    answer: str, approve: bool, exit_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: one raw USB PTY and an explicit operator response.
    app = typer.Typer()
    _ = app.command()(pairing_cli.pair_device)
    opened_ports: list[str] = []
    real_serial = serial.Serial

    def open_serial(
        port: str, baudrate: int, *, timeout: float, write_timeout: float, exclusive: bool
    ) -> serial.Serial:
        opened_ports.append(port)
        return real_serial(
            port, baudrate, timeout=timeout, write_timeout=write_timeout, exclusive=exclusive
        )

    monkeypatch.setattr(serial, "Serial", open_serial)
    with firmware_pty() as peer, ThreadPoolExecutor(max_workers=1) as workers:
        firmware = workers.submit(simulate_pairing, peer, approve=approve)
        # When: the public interactive command completes its single serial session.
        result = CliRunner().invoke(app, ["--interactive", "--port", peer.port], input=answer)
        requests = firmware.result(timeout=3)
    # Then: approval follows that response and output promptly exposes both states.
    assert result.exit_code == exit_code, result.output
    assert requests[0].action == PairingAction.OPEN
    assert "ARMED" in result.output
    assert "COMPARING" in result.output
    assert "000042" in result.output
    assert len(opened_ports) == 1


def test_manual_confirmation_parses_context_before_opening_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a confirmation whose complete comparison context is pending.
    opened_ports: list[str] = []

    def open_serial(
        port: str, _baudrate: int, *, timeout: float, write_timeout: float, exclusive: bool
    ) -> serial.Serial:
        _ = timeout, write_timeout, exclusive
        opened_ports.append(port)
        msg = "TEST_SERIAL_OPEN"
        raise PairingError(msg)

    monkeypatch.setattr(serial, "Serial", open_serial)
    # When: the shared CLI parses that operator request.
    result = CliRunner().invoke(
        cli.app, ["pair-device", "--action", "confirm", "--port", "/synthetic"]
    )
    # Then: it requests the complete comparison context at its input boundary.
    assert result.exit_code == 2
    assert "COMPARISON_CONTEXT" in result.output
    assert opened_ports == []


def test_interactive_real_pty_reports_board_declined_late_approval() -> None:
    # Given: a displayed number whose board-owned comparison deadline has passed.
    app = typer.Typer()
    _ = app.command()(pairing_cli.pair_device)
    with firmware_pty() as peer, ThreadPoolExecutor(max_workers=1) as workers:
        firmware = workers.submit(simulate_pairing, peer, approve=True, late=True)
        # When: the operator explicitly confirms the displayed context.
        result = CliRunner().invoke(app, ["--interactive", "--port", peer.port], input="yes\n")
        _ = firmware.result(timeout=3)
    # Then: the board's declined result controls the command outcome.
    assert result.exit_code == 2
    assert '"accepted":false' in result.output


def test_pairing_dispatch_uses_usb_options(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: isolated command handlers and a recording credential loader.
    loads: list[Path] = []
    observed: list[PairingOptions] = []
    monkeypatch.setattr(cli, "load", loads.append)

    def execute(options: PairingOptions) -> int:
        observed.append(options)
        return 0

    monkeypatch.setattr(pairing_cli, "execute", execute)
    # When: the shared CLI dispatches the USB pairing action.
    result = CliRunner().invoke(
        cli.app, ["pair-device", "--action", "status", "--port", "/synthetic"]
    )
    # Then: its parsed USB intent reaches the handler directly.
    assert result.exit_code == 0, result.output
    assert observed[0].action == PairingAction.STATUS
    assert observed[0].port == "/synthetic"
    assert loads == []


def test_service_dispatch_prepares_existing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a recording environment loader and isolated service handler.
    loads: list[Path] = []
    monkeypatch.setattr(cli, "load", loads.append)
    monkeypatch.setattr(environment, "inspect", lambda: 0)
    # When: an existing service command is dispatched.
    result = CliRunner().invoke(cli.app, ["env"])
    # Then: its environment is prepared exactly once before the handler.
    assert result.exit_code == 0, result.output
    assert loads == [ROOT / "config/secrets.env"]


def test_shared_help_lists_pairing_action(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the shared command set and a recording environment loader.
    loads: list[Path] = []
    monkeypatch.setattr(cli, "load", loads.append)
    # When: the operator requests command help.
    result = CliRunner().invoke(cli.app, ["--help"])
    # Then: the USB command appears in the existing help surface.
    assert result.exit_code == 0
    assert "pair-device" in result.output
    assert loads == []
