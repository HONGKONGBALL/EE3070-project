"""Independent measurement populations and clock provenance in device statistics."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from capture import decode_line, metric
from metrics import CaptureEnvelope, Metrics, Sample, analyze
from project import ROOT
from pydantic import JsonValue, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from pathlib import Path


def test_position_age_counts_independent_receipts() -> None:
    # Given one new fix, status snapshots, a retransmission and a second fix.
    samples = (
        Sample(
            type="fix_received", monotonic_ms=100, phone_session_id="p", fix_seq=1, fix_age_ms=100
        ),
        Sample(type="status", monotonic_ms=200, phone_session_id="p", fix_seq=1, fix_age_ms=4999),
        Sample(
            type="fix_received", monotonic_ms=300, phone_session_id="p", fix_seq=1, fix_age_ms=300
        ),
        Sample(
            type="fix_received", monotonic_ms=400, phone_session_id="p", fix_seq=2, fix_age_ms=200
        ),
    )
    # When the moving-location age population is analyzed.
    result = analyze(samples)
    # Then each source measurement contributes its first complete receipt once.
    assert result.fix_age_ms.count == 2
    assert result.fix_age_ms.p95 == 200
    assert result.duplicate_measurement_records == 1


def test_transport_bounds_keep_exact_latency_population_distinct() -> None:
    # Given one clock-bound delivery measurement and one unclassified latency.
    samples = (
        Sample(
            type="fix_received",
            monotonic_ms=100,
            phone_session_id="p",
            fix_seq=1,
            transport_age_upper_ms=800,
            fix_age_ms=1000,
        ),
        Sample(
            type="fix_received",
            monotonic_ms=200,
            phone_session_id="p",
            fix_seq=2,
            ble_latency_ms=12,
        ),
    )
    # When BLE delay statistics are reported.
    result = analyze(samples)
    # Then clock bounds and exact timestamps retain distinct measurement semantics.
    assert result.ble_latency_ms.count == 0
    assert result.ble_latency_upper_bound_ms.p95 == 800
    assert result.unclassified_ble_latency_records == 1


def test_each_measurement_contributes_one_conservative_latency_bound() -> None:
    # Given two bound representations for the same source measurement.
    sample = Sample(
        type="fix_received",
        monotonic_ms=100,
        phone_session_id="p",
        fix_seq=1,
        ble_latency_ms=200,
        ble_latency_kind="upper_bound",
        transport_age_upper_ms=300,
    )
    # When the latency population is formed.
    result = analyze((sample,))
    # Then the BLE-specific bound contributes one observation.
    assert result.ble_latency_upper_bound_ms.count == 1
    assert result.ble_latency_upper_bound_ms.p95 == 200


def test_expired_independent_fix_remains_in_age_population() -> None:
    # Given an expired source measurement and a fresh independent measurement.
    samples = (
        Sample(
            type="fix_received",
            monotonic_ms=100,
            phone_session_id="p",
            fix_seq=1,
            accepted=False,
            reason="FIX_EXPIRED",
            fix_age_ms=6000,
        ),
        Sample(
            type="fix_received",
            monotonic_ms=200,
            phone_session_id="p",
            fix_seq=2,
            accepted=True,
            fix_age_ms=100,
        ),
    )
    # When measurement freshness is summarized.
    result = analyze(samples)
    # Then expired input remains observable in the independent age percentile.
    assert result.fix_age_ms.count == 2
    assert result.fix_age_ms.p95 == 6000


def test_empty_populations_require_measurement_evidence() -> None:
    # Given a resource-only device observation.
    sample = Sample(type="resources", monotonic_ms=100, free_internal=100000)
    # When location and BLE statistics are requested.
    result = analyze((sample,))
    # Then their distributions require collection while resources remain observed.
    assert result.fix_age_ms.status == "waiting_condition"
    assert result.ble_latency_ms.p95 is None
    assert result.min_free_internal == 100000


def test_phone_session_distinguishes_restarted_sequence() -> None:
    # Given sequence one from two phone location sessions.
    samples = tuple(
        Sample(
            type="fix_received",
            monotonic_ms=index * 100,
            phone_session_id=session,
            fix_seq=1,
            fix_age_ms=10,
        )
        for index, session in enumerate(("phone-A", "phone-B"), 1)
    )
    # When source measurements are identified.
    result = analyze(samples)
    # Then both sessions contribute independent measurements.
    assert result.independent_measurements == 2


def test_capture_duration_uses_host_clock_across_device_reboots() -> None:
    # Given two device boots whose uptime counters have different origins.
    samples = (
        Sample(type="resources", monotonic_ms=1000000, boot_id="A", host_monotonic_ms=100),
        Sample(type="resources", monotonic_ms=1001000, boot_id="A", host_monotonic_ms=1100),
        Sample(type="resources", monotonic_ms=10, boot_id="B", host_monotonic_ms=2100),
        Sample(type="resources", monotonic_ms=1010, boot_id="B", host_monotonic_ms=3100),
    )
    # When elapsed capture time is summarized.
    result = analyze(samples)
    # Then capture duration spans the host timeline and both boot IDs survive.
    assert result.duration_ms == 3000
    assert result.duration_basis == "host_monotonic"
    assert result.boot_count == 2


def test_segment_metrics_keep_their_own_resource_window() -> None:
    # Given locked and low-power observations with distinct memory minima.
    samples = (
        Sample(type="resources", monotonic_ms=100, segment="locked", free_internal=70000),
        Sample(type="resources", monotonic_ms=200, segment="low_power", free_internal=50000),
    )
    # When the locked segment is analyzed.
    result = analyze(samples, segment="locked")
    # Then only the requested segment contributes measurements.
    assert result.samples == 1
    assert result.min_free_internal == 70000


def test_ack_device_clock_alias_is_observed() -> None:
    # Given the firmware ACK's documented monotonic field.
    raw = '{"type":"ack","device_monotonic_ms":1000,"ack_ms":12}'
    # When it crosses the metric boundary.
    sample = Sample.model_validate_json(raw)
    # Then ACK processing delay remains available for statistics.
    assert analyze((sample,)).ack_ms.p95 == 12


def test_capture_metric_retains_segment_and_host_clock() -> None:
    # Given a real serial capture wrapper around a resource record.
    captured = decode_line(b'{"type":"resources","monotonic_ms":100}\n', "locked", 2000)
    # When the capture creates its metric observation.
    sample = metric(captured)
    # Then it retains the independent host clock and segment label.
    assert sample is not None
    assert sample.host_monotonic_ms == 2000
    assert sample.segment == "locked"


def test_infinite_latency_requires_finite_metric_value() -> None:
    # Given a nonfinite latency in an input record.
    # When it crosses the parsed metric boundary.
    with pytest.raises(ValidationError):
        # Then the boundary requires a finite measurement.
        _ = Sample(type="ack", monotonic_ms=100, ack_ms=float("inf"))


def test_exact_latency_uses_declared_timing_basis() -> None:
    # Given an independently timed measurement with an explicit exact basis.
    sample = Sample(
        type="fix_received",
        monotonic_ms=100,
        phone_session_id="p",
        fix_seq=1,
        ble_latency_ms=12,
        ble_latency_kind="exact",
    )
    # When exact BLE delay is summarized.
    observed = analyze((sample,))
    # Then its timing basis contributes to the exact distribution.
    assert observed.ble_latency_ms.p95 == 12
    assert observed.ble_latency_upper_bound_ms.count == 0


def test_cli_analyzes_wrapped_segment_with_independent_fixes(tmp_path: Path) -> None:
    # Given captured measurements in two operator segments.
    rows: list[str] = []
    for sequence, segment in ((1, "locked"), (2, "locked"), (3, "low_power")):
        sample = Sample(
            type="fix_received",
            monotonic_ms=sequence * 100,
            phone_session_id="phone-A",
            fix_seq=sequence,
            fix_age_ms=sequence * 50,
            transport_age_upper_ms=20,
        )
        envelope = CaptureEnvelope(
            host_monotonic_ms=sequence * 1000,
            segment=segment,
            payload=TypeAdapter(JsonValue).validate_json(sample.model_dump_json()),
        )
        rows.append(envelope.model_dump_json())
    source = tmp_path / "capture.jsonl"
    _ = source.write_text("\n".join(rows) + "\n")
    destination = tmp_path / "metrics.json"
    command = [
        sys.executable,
        str(ROOT / "tools/metrics.py"),
        str(source),
        "--output",
        str(destination),
        "--segment",
        "locked",
    ]
    # When the real metric command analyzes the locked window.
    completed = subprocess.run(  # noqa: S603 - Fixed project CLI and temporary fixture paths.
        command, capture_output=True, text=True, check=False, timeout=30
    )
    # Then the persisted report uses that window and the independent host clock.
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = Metrics.model_validate_json(destination.read_text())
    assert report.samples == 2
    assert report.fix_age_ms.count == 2
    assert report.fix_age_ms.p95 == 100
    assert report.duration_ms == 1000
    assert report.ble_latency_ms.status == "waiting_condition"
