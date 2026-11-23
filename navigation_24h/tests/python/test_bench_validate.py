"""Offline startup capture invariants against reset and resource failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import bench_validate
from capture import decode_line

if TYPE_CHECKING:
    from pathlib import Path


def capture(tmp_path: Path, raw: tuple[str, ...]) -> Path:
    destination = tmp_path / "session.jsonl"
    _ = (tmp_path / "capture_end.json").write_text("{}\n")
    _ = destination.write_text(
        "\n".join(
            decode_line((line + "\n").encode(), "bench", 1000 + index * 100).model_dump_json()
            for index, line in enumerate(raw)
        )
        + "\n"
    )
    return destination


def healthy() -> tuple[str, ...]:
    return (
        '{"type":"diagnostic","category":"boot","reason":"NAVIGATION_READY","monotonic_ms":50,"build_id":"build-A"}',
        '{"type":"ack","command_id":"query-1","accepted":true,"ack_ms":3,"device_monotonic_ms":75}',
        '{"type":"status","state":"IDLE","device_monotonic_ms":100}',
        '{"type":"resources","monotonic_ms":5000,"boot_id":"boot-A","build_id":"build-A","free_internal":170000,"largest_internal":100000,"free_psram":8000000,"main_stack_free":4500,"reset_reason":11}',
        '{"type":"status","state":"IDLE","device_monotonic_ms":6000}',
    )


def test_ready_startup_has_correlated_ack_and_resource_headroom(tmp_path: Path) -> None:
    source = capture(tmp_path, healthy())
    result = bench_validate.validate(
        source, expected_command_ids=("query-1",), expected_build_id="build-A"
    )
    assert result.outcome == "ready"
    assert result.scope == "bench_startup_usb_status"
    assert result.main_stack.observed_bytes == 4500


def test_stack_overflow_raw_marker_requires_attention(tmp_path: Path) -> None:
    source = capture(
        tmp_path, (*healthy(), "***ERROR*** A stack overflow in task main has been detected.")
    )
    result = bench_validate.validate(source, expected_command_ids=("query-1",))
    assert result.outcome == "attention"
    assert result.stack_overflow_markers == 1


def test_startup_requires_resource_and_expected_ack_evidence(tmp_path: Path) -> None:
    source = capture(tmp_path, healthy()[:1])
    result = bench_validate.validate(source, expected_command_ids=("query-1",))
    assert result.outcome == "waiting_condition"
    assert result.internal_free.status == "waiting_condition"


def test_resource_shortfall_and_uptime_reversal_are_observed(tmp_path: Path) -> None:
    records = (
        *healthy(),
        '{"type":"resources","monotonic_ms":10,"boot_id":"boot-B","free_internal":40000,"largest_internal":8000,"free_psram":500000,"main_stack_free":500}',
    )
    result = bench_validate.validate(capture(tmp_path, records), expected_command_ids=("query-1",))
    assert result.outcome == "attention"
    assert result.boot_ids == ("boot-A", "boot-B")
    assert result.uptime_reversals == 1
    assert result.internal_free.status == "attention"


def test_ack_receipt_timestamp_keeps_its_own_clock_domain(tmp_path: Path) -> None:
    records = list(healthy())
    records[0] = records[0].replace('"monotonic_ms":50', '"monotonic_ms":76')
    result = bench_validate.validate(
        capture(tmp_path, tuple(records)), expected_command_ids=("query-1",)
    )
    assert result.uptime_reversals == 0
    assert result.maximum_ack_ms == 3
    assert result.outcome == "ready"
