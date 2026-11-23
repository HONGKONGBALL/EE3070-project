"""Device boot boundaries and independent host capture duration."""

from __future__ import annotations

from itertools import pairwise

from metrics_models import ClockEpoch, Sample, Timing


def timing(samples: tuple[Sample, ...]) -> Timing:
    """Keep device uptime epochs distinct and prefer complete host clock evidence."""
    groups: list[list[Sample]] = []
    current: list[Sample] = []
    known_boot: str | None = None
    previous = 0
    reversals = 0
    for sample in samples:
        backwards = bool(current) and sample.monotonic_ms < previous
        changed_boot = bool(sample.boot_id and known_boot and sample.boot_id != known_boot)
        inferred_reset = backwards and not (sample.boot_id and sample.boot_id == known_boot)
        reversals += backwards
        if current and (changed_boot or inferred_reset):
            groups.append(current)
            current = []
            known_boot = None
        current.append(sample)
        if sample.boot_id:
            known_boot = sample.boot_id
        previous = sample.monotonic_ms
    if current:
        groups.append(current)
    epochs = tuple(
        ClockEpoch(
            boot_id=next((item.boot_id for item in group if item.boot_id), None),
            first_monotonic_ms=min(item.monotonic_ms for item in group),
            last_monotonic_ms=max(item.monotonic_ms for item in group),
            duration_ms=max(item.monotonic_ms for item in group)
            - min(item.monotonic_ms for item in group),
            samples=len(group),
        )
        for group in groups
    )
    hosts = tuple(
        sample.host_monotonic_ms for sample in samples if sample.host_monotonic_ms is not None
    )
    host_clock = (
        bool(samples)
        and len(hosts) == len(samples)
        and all(later >= earlier for earlier, later in pairwise(hosts))
    )
    return Timing(
        duration_ms=hosts[-1] - hosts[0]
        if host_clock
        else sum(epoch.duration_ms for epoch in epochs),
        duration_basis="host_monotonic" if host_clock else "device_clock_epochs",
        epochs=epochs,
        clock_reversals=reversals,
    )
