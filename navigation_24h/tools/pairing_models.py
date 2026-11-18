"""Frozen USB owner-approval commands and state-specific device replies."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal, assert_never, override
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

MAX_REQUEST_BYTES: Final = 512
MAX_STATUS_BYTES: Final = 1024
type Epoch = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
type ComparisonNumber = Annotated[int, Field(strict=True, ge=0, le=999999)]
type Identifier = Annotated[str, Field(min_length=1, max_length=96)]


class PairingAction(StrEnum):
    """USB owner-control operations supported by the device."""

    OPEN = "open"
    CONFIRM = "confirm"
    REJECT = "reject"
    STATUS = "status"
    CLOSE = "close"


class PairingState(StrEnum):
    """Device-authoritative owner-approval progress."""

    CLOSED = "CLOSED"
    ARMED = "ARMED"
    CANDIDATE = "CANDIDATE"
    COMPARING = "COMPARING"
    APPROVED = "APPROVED"
    AUTHORIZED = "AUTHORIZED"


class PairingModel(BaseModel):
    """Immutable, strict USB JSON boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="forbid")


class RequestBase(PairingModel):
    """One independently correlated USB control command."""

    type: Literal["pairing"] = "pairing"
    command_id: Identifier = Field(default_factory=lambda: uuid4().hex)


class ControlRequest(RequestBase):
    """Window lifecycle or current-state query."""

    action: Literal[PairingAction.OPEN, PairingAction.STATUS, PairingAction.CLOSE]


class DecisionRequest(RequestBase):
    """Operator decision coupled to the displayed window, link and number."""

    action: Literal[PairingAction.CONFIRM, PairingAction.REJECT]
    window_id: Identifier
    connection_epoch: Epoch
    number: ComparisonNumber


type PairingRequest = Annotated[ControlRequest | DecisionRequest, Field(discriminator="action")]
REQUEST_ADAPTER: Final = TypeAdapter(PairingRequest)


class StatusBase(PairingModel):
    """Device-monotonic USB state and command correlation fields."""

    type: Literal["pairing_status"]
    command_id: str = Field(max_length=96)
    window_id: str = Field(max_length=96)
    connection_epoch: Epoch
    deadline_ms: int = Field(ge=0, le=2**53 - 1)
    reason: str = Field(max_length=96)
    accepted: bool | None = None


class ComparingStatus(StatusBase):
    """Current six-digit comparison offered to the USB operator."""

    state: Literal[PairingState.COMPARING]
    number: ComparisonNumber

    @property
    def digits(self) -> str:
        """Render the system comparison with leading zeroes."""
        return f"{self.number:06d}"


type ProgressState = Literal[
    PairingState.CLOSED,
    PairingState.ARMED,
    PairingState.CANDIDATE,
    PairingState.APPROVED,
    PairingState.AUTHORIZED,
]


class ProgressStatus(StatusBase):
    """Pairing window lifecycle and secure authorization progress."""

    state: ProgressState


type PairingStatus = Annotated[ComparingStatus | ProgressStatus, Field(discriminator="state")]
STATUS_ADAPTER: Final = TypeAdapter(PairingStatus)


class PairingError(Exception):
    """Typed USB failure retaining standard mutable exception traceback state."""

    code: str
    detail: str

    def __init__(self, code: str, detail: str = "") -> None:
        """Retain structured failure context throughout resource cleanup."""
        self.code = code
        self.detail = detail
        super().__init__(code, detail)

    @override
    def __str__(self) -> str:
        """Render the bounded protocol error code and operator guidance."""
        return self.code + (f": {self.detail}" if self.detail else "")


def make_request(
    action: PairingAction,
    *,
    window_id: str | None = None,
    connection_epoch: int | None = None,
    number: int | None = None,
) -> PairingRequest:
    """Parse one CLI action into its complete state-specific wire command."""
    match action:
        case PairingAction.OPEN | PairingAction.STATUS | PairingAction.CLOSE:
            return ControlRequest(action=action)
        case PairingAction.CONFIRM | PairingAction.REJECT:
            if window_id is None or connection_epoch is None or number is None:
                msg = "COMPARISON_CONTEXT"
                raise PairingError(msg, "需要 window-id、connection-epoch 与 number")
            return DecisionRequest(
                action=action, window_id=window_id, connection_epoch=connection_epoch, number=number
            )
        case unreachable:
            assert_never(unreachable)
