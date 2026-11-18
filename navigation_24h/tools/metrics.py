#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2.12,<3", "typer>=0.16,<1"]
# ///
# ─── How to run ───
# uv run --project navigation_24h navigation_24h/tools/metrics.py INPUT.jsonl
# ──────────────────
"""Independent device measurement populations and finite nearest-rank statistics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Literal, assert_never

import typer
from metrics_models import Distribution, Metrics, Sample
from metrics_timing import timing
from project import Model, save
from pydantic import ConfigDict, Field, JsonValue, ValidationError

__all__ = ("Distribution", "Metrics", "Sample", "analyze", "distribution", "percentile")


@dataclass(frozen=True, slots=True)
class Population:
    """First receipt of each independent phone measurement in one analysis window."""

    samples: tuple[Sample, ...]
    duplicate_records: int
    unidentified_records: int
    basis: Literal["independent_fix_received", "independent_status"]


class CaptureEnvelope(Model):
    """Serial capture metadata around a metric payload."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    host_monotonic_ms: int = Field(ge=0)
    segment: str = Field(max_length=64)
    payload: JsonValue


def percentile(samples: tuple[float, ...], percent: int) -> float | None:
    """Return nearest-rank percentile for a finite sample population."""
    finite = sorted(sample for sample in samples if math.isfinite(sample))
    if not finite:
        return None
    return finite[max(0, min(len(finite) - 1, math.ceil(len(finite) * percent / 100) - 1))]


def distribution(samples: tuple[float, ...]) -> Distribution:
    """Use the same finite population for counts, quantiles and collection status."""
    finite = tuple(sample for sample in samples if math.isfinite(sample))
    return Distribution(
        count=len(finite),
        p50=percentile(finite, 50),
        p95=percentile(finite, 95),
        p99=percentile(finite, 99),
        status="observed" if finite else "waiting_condition",
    )


def measurement_population(samples: tuple[Sample, ...]) -> Population:
    """Deduplicate independent fixes by phone session and source measurement sequence."""
    receipts = tuple(sample for sample in samples if sample.type == "fix_received")
    candidates = receipts or tuple(sample for sample in samples if sample.type == "status")
    seen: set[tuple[str, int]] = set()
    selected: list[Sample] = []
    duplicates = unidentified = 0
    for sample in candidates:
        if not sample.phone_session_id or sample.fix_seq is None or sample.fix_seq == 0:
            unidentified += 1
            continue
        identity = (sample.phone_session_id, sample.fix_seq)
        if identity in seen or sample.reason in {"FIX_SEQUENCE", "FIX_SOURCE_DUPLICATE"}:
            duplicates += 1
            continue
        seen.add(identity)
        selected.append(sample)
    return Population(
        samples=tuple(selected),
        duplicate_records=duplicates,
        unidentified_records=unidentified,
        basis="independent_fix_received" if receipts else "independent_status",
    )


def analyze(samples: tuple[Sample, ...], *, segment: str | None = None) -> Metrics:
    """Summarize one capture window using independent location receipts and clock provenance."""
    window = tuple(sample for sample in samples if segment is None or sample.segment == segment)
    population = measurement_population(window)
    fixes = population.samples
    exact: list[float] = []
    bounds: list[float] = []
    unclassified = 0
    for sample in fixes:
        bound = sample.ble_latency_upper_bound_ms
        match sample.ble_latency_kind:
            case "exact":
                if sample.ble_latency_ms is not None:
                    exact.append(sample.ble_latency_ms)
            case "upper_bound":
                if bound is None:
                    bound = sample.ble_latency_ms
            case None:
                unclassified += sample.ble_latency_ms is not None
            case unreachable:
                assert_never(unreachable)
        if bound is None:
            bound = sample.transport_age_upper_ms
        if bound is not None:
            bounds.append(bound)
    clocks = timing(window)
    return Metrics(
        samples=len(window),
        duration_ms=clocks.duration_ms,
        duration_basis=clocks.duration_basis,
        fix_age_ms=distribution(
            tuple(sample.fix_age_ms for sample in fixes if sample.fix_age_ms is not None)
        ),
        source_age_ms=distribution(
            tuple(sample.source_age_ms for sample in fixes if sample.source_age_ms is not None)
        ),
        ble_latency_ms=distribution(tuple(exact)),
        ble_latency_upper_bound_ms=distribution(tuple(bounds)),
        processing_ms=distribution(
            tuple(sample.processing_ms for sample in fixes if sample.processing_ms is not None)
        ),
        ack_ms=distribution(
            tuple(
                sample.ack_ms
                for sample in window
                if sample.type == "ack" and sample.ack_ms is not None
            )
        ),
        min_free_internal=min(
            (sample.free_internal for sample in window if sample.free_internal is not None),
            default=None,
        ),
        min_largest_internal=min(
            (sample.largest_internal for sample in window if sample.largest_internal is not None),
            default=None,
        ),
        min_free_psram=min(
            (sample.free_psram for sample in window if sample.free_psram is not None), default=None
        ),
        min_main_stack_free=min(
            (sample.main_stack_free for sample in window if sample.main_stack_free is not None),
            default=None,
        ),
        boot_count=len({sample.boot_id for sample in window if sample.boot_id is not None}),
        clock_epochs=clocks.epochs,
        clock_reversals=clocks.clock_reversals,
        independent_measurements=len(fixes),
        duplicate_measurement_records=population.duplicate_records,
        unidentified_measurement_records=population.unidentified_records,
        unclassified_ble_latency_records=unclassified,
        fix_age_population=population.basis,
        segment=segment,
    )


def parse_metric_line(line: str) -> Sample | None:
    """Parse a direct metric or a serial capture envelope at the file boundary."""
    try:
        return Sample.model_validate_json(line)
    except ValidationError:
        try:
            envelope = CaptureEnvelope.model_validate_json(line)
            sample = Sample.model_validate(envelope.payload)
        except ValidationError:
            return None
        return sample.model_copy(
            update={"host_monotonic_ms": envelope.host_monotonic_ms, "segment": envelope.segment}
        )


DEFAULT_OUTPUT: Final = Path("metrics.json")


def main(source: Path, output: Path = DEFAULT_OUTPUT, segment: str | None = None) -> None:
    """Analyze device JSONL or a wrapped capture for the selected segment."""
    samples: list[Sample] = []
    ignored = 0
    with source.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            sample = parse_metric_line(line)
            if sample is None:
                ignored += 1
            else:
                samples.append(sample)
    result = analyze(tuple(samples), segment=segment).model_copy(
        update={"ignored_input_records": ignored}
    )
    save(output, result)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    typer.run(main)
