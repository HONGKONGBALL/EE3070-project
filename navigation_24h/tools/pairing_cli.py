"""USB numeric comparison with explicit operator approval."""

from __future__ import annotations

from time import monotonic
from typing import Annotated, assert_never

import serial
import typer
from pairing_models import (
    ComparingStatus,
    ControlRequest,
    DecisionRequest,
    PairingAction,
    PairingError,
    PairingModel,
    PairingState,
    PairingStatus,
    ProgressStatus,
    make_request,
)
from pairing_serial import PairingSerial
from project import config
from pydantic import ValidationError


class PairingOptions(PairingModel):
    """Parsed CLI intent for one serial connection."""

    port: str = ""
    action: PairingAction = PairingAction.STATUS
    interactive: bool = False
    window_id: str | None = None
    connection_epoch: int | None = None
    number: int | None = None


def show_status(status: PairingStatus) -> None:
    """Publish each USB state immediately for the operator."""
    print(status.model_dump_json(exclude_none=True), flush=True)
    match status:
        case ComparingStatus():
            print(f"USB 配对数字：{status.digits}，请逐位核对 iPhone 系统配对弹窗。", flush=True)
        case ProgressStatus():
            return
        case unreachable:
            assert_never(unreachable)


def _wait_authorized(session: PairingSerial, request: DecisionRequest) -> int:
    """Wait for the current confirmed link to complete secure pairing."""
    deadline = monotonic() + 30
    while True:
        status = session.receive(deadline)
        if (
            status.window_id != request.window_id
            or status.connection_epoch != request.connection_epoch
        ):
            continue
        match status.state:
            case PairingState.AUTHORIZED:
                if status.command_id == request.command_id and status.accepted is not False:
                    return 0
            case PairingState.CLOSED:
                msg = "PAIRING_CLOSED"
                raise PairingError(msg, status.reason)
            case (
                PairingState.ARMED
                | PairingState.CANDIDATE
                | PairingState.APPROVED
                | PairingState.COMPARING
            ):
                continue
            case unreachable:
                assert_never(unreachable)


def _decide(session: PairingSerial, comparison: ComparingStatus) -> int:
    """Ask the operator once and send the exact displayed comparison context."""
    try:
        approved = typer.confirm(
            f"iPhone 系统弹窗与 USB 的六位数字 {comparison.digits} 是否完全一致？",
            default=False,
        )
    except typer.Abort:
        approved = False
    request = DecisionRequest(
        action=PairingAction.CONFIRM if approved else PairingAction.REJECT,
        window_id=comparison.window_id,
        connection_epoch=comparison.connection_epoch,
        number=comparison.number,
    )
    reply = session.exchange(request)
    if reply.accepted is not True or not approved:
        return 2
    match reply.state:
        case PairingState.AUTHORIZED:
            if (
                reply.window_id == request.window_id
                and reply.connection_epoch == request.connection_epoch
            ):
                return 0
            msg = "PAIRING_CONTEXT"
            raise PairingError(msg)
        case (
            PairingState.ARMED
            | PairingState.CANDIDATE
            | PairingState.APPROVED
            | PairingState.COMPARING
            | PairingState.CLOSED
        ):
            return _wait_authorized(session, request)
        case unreachable:
            assert_never(unreachable)


def interactive_pairing(session: PairingSerial) -> int:
    """Open a board-timed window and carry one comparison through authorization."""
    request = ControlRequest(action=PairingAction.OPEN)
    status = session.exchange(request)
    if status.accepted is not True:
        return 2
    deadline = monotonic() + 65
    while True:
        if status.command_id == request.command_id:
            match status:
                case ComparingStatus():
                    return _decide(session, status)
                case ProgressStatus():
                    match status.state:
                        case PairingState.CLOSED:
                            msg = "PAIRING_CLOSED"
                            raise PairingError(msg, status.reason)
                        case PairingState.AUTHORIZED:
                            msg = "COMPARISON_CONTEXT"
                            raise PairingError(msg, "请核对当前配对窗口")
                        case PairingState.ARMED | PairingState.CANDIDATE | PairingState.APPROVED:
                            pass
                        case unreachable:
                            assert_never(unreachable)
                case unreachable:
                    assert_never(unreachable)
        status = session.receive(deadline)


def execute(options: PairingOptions) -> int:
    """Run one manual action or one complete interactive USB session."""
    if options.interactive:
        match options.action:
            case PairingAction.OPEN | PairingAction.STATUS:
                request = None
            case PairingAction.CONFIRM | PairingAction.REJECT | PairingAction.CLOSE:
                msg = "INTERACTIVE_ACTION"
                raise PairingError(msg, "交互流程从 open 开始")
            case unreachable:
                assert_never(unreachable)
    else:
        request = make_request(
            options.action,
            window_id=options.window_id,
            connection_epoch=options.connection_epoch,
            number=options.number,
        )
    port = options.port or config().esp32.port
    if not port:
        msg = "SERIAL_PORT"
        raise PairingError(msg, "请指定 --port 或配置 ESP32 USB 串口")
    with serial.Serial(port, 115200, timeout=0.2, write_timeout=2, exclusive=True) as connection:
        session = PairingSerial(connection, show_status)
        if request is None:
            return interactive_pairing(session)
        reply = session.exchange(request)
        return 0 if reply.accepted else 2


def pair_device(
    action: Annotated[PairingAction, typer.Option()] = PairingAction.STATUS,
    interactive: Annotated[bool, typer.Option("--interactive")] = False,
    port: Annotated[str, typer.Option()] = "",
    window_id: Annotated[str | None, typer.Option()] = None,
    connection_epoch: Annotated[int | None, typer.Option(min=0, max=2**32 - 1)] = None,
    number: Annotated[int | None, typer.Option(min=0, max=999999)] = None,
) -> None:
    """Open a USB pairing window, compare six digits and approve the current link."""
    try:
        result = execute(
            PairingOptions(
                action=action,
                interactive=interactive,
                port=port,
                window_id=window_id,
                connection_epoch=connection_epoch,
                number=number,
            )
        )
    except (PairingError, ValidationError, serial.SerialException, OSError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    raise typer.Exit(result)
