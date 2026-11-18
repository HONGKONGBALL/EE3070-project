"""Pure evidence gate for local corridors, without claiming autonomous safety.

A monocular detection box supplies neither depth nor free ground and can never
approve a corridor. This module consumes measured depth/ground evidence over a
continuous interval. Missing evidence, motion, stale samples and contradictions
revoke its result immediately. A controller still owns stopping and steering.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class EvidenceSource(StrEnum):
    MONOCULAR = "monocular"
    DEPTH_GROUND = "depth_ground"


@dataclass(frozen=True, slots=True)
class CorridorObservation:
    path_version: int
    timestamp: float
    source: EvidenceSource
    free_width_m: float
    free_distance_m: float
    ground_supported: bool
    drop_detected: bool = False
    obstacle_detected: bool = False
    stationary: bool = True


@dataclass(frozen=True, slots=True)
class CorridorDecision:
    path_version: int
    revision: int
    eligible: bool
    reason: str
    expires_at: float


class CorridorGate:
    """Mutable evidence accumulator. Resetting it permanently revokes old tokens."""

    def __init__(self, min_width_m: float = 0.9, min_distance_m: float = 1.5,
                 continuous_s: float = 0.6, max_gap_s: float = 0.25, max_age_s: float = 0.3) -> None:
        self.min_width_m: float = min_width_m
        self.min_distance_m: float = min_distance_m
        self.continuous_s: float = continuous_s
        self.max_gap_s: float = max_gap_s
        self.max_age_s: float = max_age_s
        self.path_version: int = 0
        self.revision: int = 0
        self._since: float | None = None
        self._last: float | None = None
        self._latest: CorridorDecision | None = None

    def reset(self, path_version: int) -> None:
        """Revoke any previous eligibility when a route or desired side changes."""
        self.path_version = path_version
        self.revision += 1
        self._since = None
        self._last = None
        self._latest = None

    def observe(self, sample: CorridorObservation, now: float) -> CorridorDecision:
        """Accept chronological, recent measured evidence; fail closed otherwise."""
        numeric = (sample.timestamp, now, sample.free_width_m, sample.free_distance_m)
        valid = all(math.isfinite(value) for value in numeric)
        valid = valid and sample.path_version == self.path_version
        valid = valid and 0 <= now - sample.timestamp <= self.max_age_s
        valid = valid and (self._last is None or sample.timestamp > self._last)
        valid = valid and sample.source is EvidenceSource.DEPTH_GROUND
        valid = valid and sample.ground_supported and sample.stationary
        valid = valid and not (sample.drop_detected or sample.obstacle_detected)
        valid = valid and sample.free_width_m >= self.min_width_m
        valid = valid and sample.free_distance_m >= self.min_distance_m
        if not valid:
            self.reset(self.path_version)
            return CorridorDecision(self.path_version, self.revision, False, "insufficient_or_unsafe_evidence", now)
        if self._last is None or sample.timestamp - self._last > self.max_gap_s:
            self.reset(self.path_version)
            self._since = sample.timestamp
        self._last = sample.timestamp
        if self._since is None:
            self._since = sample.timestamp
        eligible = sample.timestamp - self._since >= self.continuous_s
        decision = CorridorDecision(self.path_version, self.revision, eligible,
                                    "measured_continuous_corridor" if eligible else "collecting_evidence",
                                    sample.timestamp + self.max_age_s)
        self._latest = decision
        return decision

    def is_current(self, decision: CorridorDecision, now: float) -> bool:
        """Replays cannot resurrect an expired, contradicted or replaced corridor."""
        return (decision.eligible and decision == self._latest and decision.path_version == self.path_version
                and decision.revision == self.revision and self._last is not None
                and 0 <= now - self._last <= self.max_age_s and now <= decision.expires_at)
