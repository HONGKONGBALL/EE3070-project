"""Browser decisions retain the exact current USB comparison and deadline."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pairing_models import (
    ComparingStatus,
    ControlRequest,
    DecisionRequest,
    PairingError,
    PairingState,
    ProgressStatus,
)
from pairing_web_state import DecisionAction, OpenAction, WebController


@dataclass(frozen=True, slots=True)
class Prepared:
    state: WebController
    clock: list[int]
    sent: list[ControlRequest | DecisionRequest]


@pytest.fixture
def prepared() -> Prepared:
    clock = [1000]
    state = WebController(lambda: clock[0])
    sent: list[ControlRequest | DecisionRequest] = []
    state.enqueue(OpenAction(action="open"))
    state.dispatch(sent.append)
    state.observe(
        ProgressStatus(
            type="pairing_status",
            state=PairingState.ARMED,
            command_id=sent[0].command_id,
            window_id="window",
            connection_epoch=0,
            deadline_ms=70000,
            accepted=True,
            reason="ARMED",
        )
    )
    return Prepared(state, clock, sent)


def comparison(prepared: Prepared) -> DecisionAction:
    prepared.state.observe(
        ComparingStatus(
            type="pairing_status",
            state=PairingState.COMPARING,
            command_id=prepared.sent[0].command_id,
            window_id="window",
            connection_epoch=7,
            deadline_ms=35000,
            number=42,
            reason="COMPARE_NUMBER",
            accepted=True,
        )
    )
    return DecisionAction(
        action="confirm",
        window_id="window",
        connection_epoch=7,
        number=42,
        revision=prepared.state.view().revision,
    )


def test_armed_window_keeps_open_reserved_for_next_closed_window(prepared: Prepared) -> None:
    assert not prepared.state.view().can_open
    with pytest.raises(PairingError, match="PAIRING_BUSY"):
        prepared.state.enqueue(OpenAction(action="open"))


def test_stray_closed_event_preserves_pending_open() -> None:
    state = WebController()
    state.enqueue(OpenAction(action="open"))
    state.observe(
        ProgressStatus(
            type="pairing_status",
            state=PairingState.CLOSED,
            command_id="",
            window_id="",
            connection_epoch=0,
            deadline_ms=0,
            accepted=False,
            reason="DISCONNECTED",
        )
    )
    assert not state.view().can_open
    with pytest.raises(PairingError, match="PAIRING_BUSY"):
        state.enqueue(OpenAction(action="open"))


def test_explicit_current_choice_dispatches_once(prepared: Prepared) -> None:
    action = comparison(prepared)
    prepared.state.enqueue(action)
    with pytest.raises(PairingError, match="PAIRING_CONTEXT"):
        prepared.state.enqueue(action)
    prepared.state.dispatch(prepared.sent.append)
    assert len(prepared.sent) == 2
    assert isinstance(prepared.sent[1], DecisionRequest)
    assert prepared.sent[1].number == 42


def test_delayed_comparison_uses_open_anchor_and_expires(prepared: Prepared) -> None:
    prepared.clock[0] = 26000
    action = comparison(prepared)
    assert not prepared.state.view().can_decide
    with pytest.raises(PairingError, match="PAIRING_CONTEXT"):
        prepared.state.enqueue(action)


def test_queued_choice_rechecks_current_device_state(prepared: Prepared) -> None:
    action = comparison(prepared)
    prepared.state.enqueue(action)
    prepared.state.observe(
        ProgressStatus(
            type="pairing_status",
            state=PairingState.CLOSED,
            command_id=prepared.sent[0].command_id,
            window_id="window",
            connection_epoch=7,
            deadline_ms=0,
            accepted=False,
            reason="TIMEOUT",
        )
    )
    prepared.state.dispatch(prepared.sent.append)
    assert len(prepared.sent) == 1


@pytest.mark.parametrize(
    ("window", "epoch", "number", "revision_delta"),
    [("earlier", 7, 42, 0), ("window", 8, 42, 0), ("window", 7, 43, 0), ("window", 7, 42, 1)],
)
def test_browser_choice_requires_exact_displayed_context(
    prepared: Prepared, window: str, epoch: int, number: int, revision_delta: int
) -> None:
    # Given: a live comparison and one mismatching browser context component.
    current = comparison(prepared)
    stale = DecisionAction(
        action="confirm",
        window_id=window,
        connection_epoch=epoch,
        number=number,
        revision=current.revision + revision_delta,
    )
    # When / Then: the boundary retains an empty serial-owner queue.
    with pytest.raises(PairingError, match="PAIRING_CONTEXT"):
        prepared.state.enqueue(stale)
    assert prepared.state.queue.empty()


def test_repeated_comparison_keeps_original_device_deadline(prepared: Prepared) -> None:
    # Given: a window anchored by its own OPEN response.
    _ = comparison(prepared)
    deadline = prepared.clock[0] + prepared.state.view().remaining_ms
    prepared.clock[0] += 4000
    # When: the same comparison is observed again later.
    _ = comparison(prepared)
    # Then: its conservative host expiry remains fixed.
    assert prepared.clock[0] + prepared.state.view().remaining_ms == deadline
