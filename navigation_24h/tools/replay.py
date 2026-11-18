#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2.12,<3", "typer>=0.16,<1", "pyserial>=3.5,<4"]
# ///
# ─── How to run ───
# uv run --project navigation_24h navigation_24h/tools/replay.py CAPTURE.jsonl
# ──────────────────
"""Deterministic reconstruction of collected serial state and metric records."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path  # noqa: TC003 - Typer resolves this annotation at runtime.

import typer
from capture import CaptureLine, metric
from metrics import analyze
from project import Model, save
from pydantic import JsonValue  # noqa: TC002 - Pydantic resolves model annotations at runtime.


class ReplayReport(Model):
    """Input ordering and observation count from a persisted capture."""

    input_path: str
    records: int
    malformed: int
    chronological: bool
    segments: tuple[str, ...]
    timeline: tuple[JsonValue, ...]


def run(source: Path) -> int:
    """Reconstruct the observed timeline without advancing a live device clock."""
    lines = tuple(
        CaptureLine.model_validate_json(line)
        for line in source.read_text().splitlines()
        if line.strip()
    )
    samples = tuple(value for line in lines if (value := metric(line)) is not None)
    report = ReplayReport(
        input_path=str(source),
        records=len(lines),
        malformed=sum(not line.valid_json for line in lines),
        chronological=all(
            first.host_monotonic_ms <= second.host_monotonic_ms for first, second in pairwise(lines)
        ),
        segments=tuple(dict.fromkeys(line.segment for line in lines)),
        timeline=tuple(line.payload for line in lines if line.valid_json),
    )
    output = source.parent / f"{source.stem}-replay.json"
    save(output, report)
    save(source.parent / f"{source.stem}-metrics.json", analyze(samples))
    print(report.model_dump_json(indent=2))
    return 0 if report.chronological and report.records else 2


def main(source: Path) -> None:
    """Replay a captured JSONL session into timestamped reports."""
    raise typer.Exit(run(source))


if __name__ == "__main__":
    typer.run(main)
