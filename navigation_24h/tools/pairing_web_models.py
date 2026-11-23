"""Frozen browser pairing intents and state projections."""

from typing import Annotated, Final, Literal, assert_never

from pairing_models import PairingAction, PairingModel, PairingState, PairingStatus
from pydantic import Field, TypeAdapter


class OpenAction(PairingModel):
    """Explicit browser request to open the next pairing window."""

    action: Literal["open"]


class DecisionAction(PairingModel):
    """One browser choice bound to the displayed comparison revision."""

    action: Literal["confirm", "reject"]
    window_id: str = Field(min_length=1, max_length=96)
    connection_epoch: int = Field(ge=0, le=2**32 - 1)
    number: int = Field(ge=0, le=999999)
    revision: int = Field(ge=1)


type WebAction = Annotated[OpenAction | DecisionAction, Field(discriminator="action")]
ACTION_ADAPTER: Final = TypeAdapter(WebAction)


class WebView(PairingModel):
    """Immutable browser projection of the current serial session."""

    revision: int
    status: PairingStatus | None
    remaining_ms: int
    can_decide: bool
    can_open: bool
    authorized: bool
    message: str


def decision_kind(
    action: Literal["confirm", "reject"],
) -> Literal[PairingAction.CONFIRM, PairingAction.REJECT]:
    """Translate an exhaustive explicit browser choice into its wire action."""
    match action:
        case "confirm":
            return PairingAction.CONFIRM
        case "reject":
            return PairingAction.REJECT
        case unreachable:
            assert_never(unreachable)


def state_message(state: PairingState) -> str:
    """Describe the current device step in the browser."""
    match state:
        case PairingState.ARMED:
            return "配对窗口已开启，请在 iPhone 选择导航设备"
        case PairingState.CANDIDATE:
            return "设备已连接，正在准备六位数字"
        case PairingState.COMPARING:
            return "核对手机数字一致 → 手机点配对 → 本页确认一致"
        case PairingState.APPROVED:
            return "数字确认已接收，正在建立安全连接"
        case PairingState.AUTHORIZED:
            return "设备安全连接已建立，正在记录绑定状态"
        case PairingState.CLOSED:
            return "点击开始配对，开启下一次数字核对"
        case unreachable:
            assert_never(unreachable)
