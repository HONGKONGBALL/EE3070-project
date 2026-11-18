"""Locked browser intent queue and device-authoritative pairing context."""

from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Lock
from time import monotonic_ns
from typing import TYPE_CHECKING, Final, assert_never

from pairing_models import (
    ComparingStatus,
    ControlRequest,
    DecisionRequest,
    PairingAction,
    PairingError,
    PairingState,
    PairingStatus,
    ProgressStatus,
)
from pairing_web_models import (
    ACTION_ADAPTER,
    DecisionAction,
    OpenAction,
    WebAction,
    WebView,
    decision_kind,
    state_message,
)

__all__ = ("ACTION_ADAPTER", "DecisionAction", "OpenAction", "WebController", "WebView")

if TYPE_CHECKING:
    from collections.abc import Callable


ARM_WINDOW_MS: Final = 60000


def comparison_context(status: PairingStatus) -> tuple[PairingState, str, int, int, int | None]:
    """Extract a typed immutable identity for a displayed device phase."""
    match status:
        case ComparingStatus():
            number = status.number
        case ProgressStatus():
            number = None
        case unreachable:
            assert_never(unreachable)
    return status.state, status.window_id, status.connection_epoch, status.deadline_ms, number


def milliseconds() -> int:
    """Read host monotonic milliseconds for conservative USB deadlines."""
    return monotonic_ns() // 1_000_000


class WebController:
    """Mutable state shared under one lock; serial work is owned by the worker thread."""

    def __init__(self, clock: Callable[[], int] = milliseconds) -> None:
        """Initialize the bounded immutable-intent queue and timing anchor."""
        self.lock: Lock = Lock()
        self.queue: Queue[WebAction] = Queue(maxsize=8)
        self.clock: Callable[[], int] = clock
        self.status: PairingStatus | None = None
        self.revision: int = 0
        self.message: str = "点击开始配对，然后在 iPhone 选择导航设备"
        self.open_id: str = ""
        self.open_sent_ms: int = 0
        self.window_id: str = ""
        self.offset_upper_ms: int | None = None
        self.reserved: bool = False
        self.opening: bool = False
        self.confirmation: DecisionRequest | None = None

    def _remaining(self) -> int:
        if self.status is None or self.offset_upper_ms is None:
            return 0
        if self.status.window_id != self.window_id or self.status.deadline_ms == 0:
            return 0
        return max(0, self.status.deadline_ms - self.offset_upper_ms - self.clock() - 500)

    def _matches(self, action: DecisionAction) -> bool:
        match self.status:
            case ComparingStatus() as status:
                return (
                    action.window_id == status.window_id == self.window_id
                    and action.connection_epoch == status.connection_epoch
                    and action.number == status.number
                    and action.revision == self.revision
                    and self._remaining() > 0
                )
            case ProgressStatus() | None:
                return False
            case unreachable:
                assert_never(unreachable)

    def _can_open(self) -> bool:
        match self.status:
            case None | ProgressStatus(state=PairingState.CLOSED):
                return not self.opening and not self.reserved
            case ComparingStatus() | ProgressStatus():
                return False
            case unreachable:
                assert_never(unreachable)

    def view(self) -> WebView:
        """Copy the locked state for a browser poll."""
        with self.lock:
            match self.status:
                case ComparingStatus():
                    comparing = True
                case ProgressStatus() | None:
                    comparing = False
                case unreachable:
                    assert_never(unreachable)
            authorized = bool(
                self.confirmation is not None
                and self.status is not None
                and self.status.state == PairingState.AUTHORIZED
                and self.status.command_id == self.confirmation.command_id
                and self.status.window_id == self.confirmation.window_id
                and self.status.connection_epoch == self.confirmation.connection_epoch
                and self.status.accepted is True
            )
            return WebView(
                revision=self.revision,
                status=self.status,
                remaining_ms=self._remaining(),
                can_decide=comparing and not self.reserved and self._remaining() > 0,
                can_open=self._can_open(),
                authorized=authorized,
                message=self.message,
            )

    def enqueue(self, action: WebAction) -> None:
        """Reserve one explicit browser action against the current context."""
        with self.lock:
            match action:
                case OpenAction():
                    if not self._can_open():
                        msg = "PAIRING_BUSY"
                        raise PairingError(msg)
                case DecisionAction():
                    if self.reserved or not self._matches(action):
                        msg = "PAIRING_CONTEXT"
                        raise PairingError(msg)
                case unreachable:
                    assert_never(unreachable)
            try:
                self.queue.put_nowait(action)
            except Full as error:
                msg = "PAIRING_BUSY"
                raise PairingError(msg) from error
            match action:
                case OpenAction():
                    self.opening = True
                    self.open_id = ""
                case DecisionAction():
                    self.reserved = True
                case unreachable:
                    assert_never(unreachable)

    def dispatch(self, send: Callable[[ControlRequest | DecisionRequest], None]) -> None:
        """Recheck a queued context immediately before the serial owner sends it."""
        try:
            action = self.queue.get_nowait()
        except Empty:
            return
        with self.lock:
            match action:
                case OpenAction():
                    request = ControlRequest(action=PairingAction.OPEN)
                    self.open_id = request.command_id
                    self.open_sent_ms = self.clock()
                    self.window_id = ""
                    self.offset_upper_ms = None
                    self.confirmation = None
                    self.status = None
                    self.reserved = False
                    self.revision += 1
                    self.message = "正在开启配对窗口"
                case DecisionAction():
                    if not self._matches(action):
                        self.message = "请等待设备更新后重新开始配对"
                        return
                    request = DecisionRequest(
                        action=decision_kind(action.action),
                        window_id=action.window_id,
                        connection_epoch=action.connection_epoch,
                        number=action.number,
                    )
                    self.confirmation = request
                    self.message = "已提交选择，正在等待设备确认"
                case unreachable:
                    assert_never(unreachable)
        send(request)

    def observe(self, status: PairingStatus) -> None:
        """Apply ordered device events and anchor only a correlated OPEN result."""
        with self.lock:
            if self.opening and (not self.open_id or status.command_id != self.open_id):
                return
            if (
                status.command_id == self.open_id
                and status.state == PairingState.ARMED
                and status.accepted is True
                and status.deadline_ms >= ARM_WINDOW_MS
                and self.offset_upper_ms is None
            ):
                self.window_id = status.window_id
                self.offset_upper_ms = status.deadline_ms - ARM_WINDOW_MS - self.open_sent_ms
            if self.window_id and status.window_id != self.window_id:
                return
            previous = self.status
            self.status = status
            if previous is None or comparison_context(previous) != comparison_context(status):
                self.revision += 1
            self.opening = False
            self.reserved = self.reserved and status.state != PairingState.CLOSED
            self.message = state_message(status.state)

    def fail(self, message: str) -> None:
        """Publish a safe worker failure while clearing actionable context."""
        with self.lock:
            self.offset_upper_ms = None
            self.opening = False
            self.reserved = False
            self.message = message
