"""USB pairing request and comparison boundary contracts."""

from __future__ import annotations

import pytest
from pairing_models import (
    REQUEST_ADAPTER,
    STATUS_ADAPTER,
    ComparingStatus,
    PairingAction,
    PairingError,
    PairingRequest,
    PairingStatus,
    make_request,
)
from project import ROOT
from pydantic import JsonValue, TypeAdapter, ValidationError


def test_comparing_status_preserves_leading_zero_display() -> None:
    # Given: a current USB numeric comparison.
    raw = (
        '{"type":"pairing_status","state":"COMPARING","command_id":"open",'
        '"window_id":"window","connection_epoch":7,"number":42,"deadline_ms":25000,'
        '"reason":"COMPARE_NUMBER","accepted":true}'
    )
    # When: its typed boundary parses the frame.
    status = STATUS_ADAPTER.validate_json(raw)
    # Then: the six-digit comparison retains all digits.
    assert isinstance(status, ComparingStatus)
    assert status.digits == "000042"


@pytest.mark.parametrize("number", ["null", "-1", "1000000", '"123456"', "true"])
def test_comparison_number_requires_bounded_integer(number: str) -> None:
    # Given: a comparison frame with an invalid number value.
    raw = (
        '{"type":"pairing_status","state":"COMPARING","command_id":"open",'
        '"window_id":"window","connection_epoch":7,"deadline_ms":25000,'
        f'"number":{number},"reason":"COMPARE_NUMBER"}}'
    )
    # When / Then: the boundary reports a typed validation result.
    with pytest.raises(ValidationError):
        _ = STATUS_ADAPTER.validate_json(raw)


def test_comparison_frame_requires_number() -> None:
    # Given: a COMPARING event carrying its current peer context.
    raw = (
        '{"type":"pairing_status","state":"COMPARING","command_id":"open",'
        '"window_id":"window","connection_epoch":7,"deadline_ms":25000,'
        '"reason":"COMPARE_NUMBER"}'
    )
    # When / Then: parsing requires the numeric comparison value.
    with pytest.raises(ValidationError, match="number"):
        _ = STATUS_ADAPTER.validate_json(raw)


def test_closed_status_has_state_specific_fields() -> None:
    # Given: a closed USB pairing window carrying comparison data.
    raw = (
        '{"type":"pairing_status","state":"CLOSED","command_id":"close",'
        '"window_id":"window","connection_epoch":7,"number":42,"deadline_ms":0,'
        '"reason":"CLOSED","accepted":true}'
    )
    # When / Then: the closed-state boundary enforces its field set.
    with pytest.raises(ValidationError, match="number"):
        _ = STATUS_ADAPTER.validate_json(raw)


def test_confirm_request_requires_complete_comparison_context() -> None:
    # Given: an operator confirmation with its window identity alone.
    # When / Then: request creation requires epoch and number together.
    with pytest.raises(PairingError, match="COMPARISON_CONTEXT"):
        _ = make_request(PairingAction.CONFIRM, window_id="window")


def test_confirm_request_round_trips_frozen_context() -> None:
    # Given: the displayed comparison and an explicit operator choice.
    request = make_request(PairingAction.CONFIRM, window_id="window", connection_epoch=7, number=42)
    # When: the command crosses its JSON boundary.
    parsed = REQUEST_ADAPTER.validate_json(request.model_dump_json())
    # Then: action and current context stay coupled.
    assert parsed == request
    with pytest.raises(ValidationError, match="frozen"):
        parsed.command_id = "changed"


def test_schema_matches_frozen_usb_boundary() -> None:
    # Given: the generated USB pairing schema.
    schema = TypeAdapter(JsonValue).validate_json(
        (ROOT / "protocol/pairing.schema.json").read_text()
    )
    # When: the same frozen request and response types generate the contract.
    generated = TypeAdapter(PairingRequest | PairingStatus).json_schema()
    # Then: the published schema is exactly reproducible from the boundary models.
    assert schema == generated
