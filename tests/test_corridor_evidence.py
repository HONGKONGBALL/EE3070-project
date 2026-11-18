"""Local steering permission requires continuous measured corridor evidence."""

from cane.nav.corridor import CorridorGate, CorridorObservation, EvidenceSource


def observation(t: float, source: EvidenceSource = EvidenceSource.DEPTH_GROUND, obstacle: bool = False) -> CorridorObservation:
    return CorridorObservation(4, t, source, 1.2, 2.0, True, obstacle_detected=obstacle)


def ready_gate() -> CorridorGate:
    gate = CorridorGate(continuous_s=0.4)
    gate.reset(4)
    gate.observe(observation(1.0), 1.0)
    gate.observe(observation(1.2), 1.2)
    return gate


def test_monocular_boxes_never_approve_corridor():
    # Given only a camera detection box without depth/ground measurements.
    gate = CorridorGate(continuous_s=0)
    gate.reset(4)
    # When it is evaluated.
    decision = gate.observe(observation(1, EvidenceSource.MONOCULAR), 1)
    # Then the corridor remains ineligible even with plausible numeric fields.
    assert not decision.eligible


def test_continuous_ground_and_depth_evidence_is_eligible():
    # Given chronological depth/ground observations spanning the required interval.
    gate = ready_gate()
    # When another uninterrupted observation arrives.
    decision = gate.observe(observation(1.41), 1.41)
    # Then its permission token is valid only for the current route and interval.
    assert decision.eligible
    assert gate.is_current(decision, 1.5)
    assert not gate.is_current(decision, 2.0)


def test_dynamic_obstacle_revokes_previous_direction_permanently():
    # Given a currently eligible corridor token.
    gate = ready_gate()
    decision = gate.observe(observation(1.41), 1.41)
    # When a new obstacle contradicts that evidence.
    gate.observe(observation(1.5, obstacle=True), 1.5)
    # Then repeating the old token cannot revive it.
    assert not gate.is_current(decision, 1.5)


def test_path_change_revokes_previous_direction():
    # Given an eligible old path.
    gate = ready_gate()
    decision = gate.observe(observation(1.41), 1.41)
    # When a new route is selected.
    gate.reset(5)
    # Then the old route token cannot be repeated.
    assert not gate.is_current(decision, 1.5)


def test_evidence_gap_starts_continuous_interval_again():
    # Given partially accumulated observations.
    gate = ready_gate()
    # When a sensor gap exceeds the allowed interval.
    decision = gate.observe(observation(2), 2)
    # Then old samples do not establish continuous evidence.
    assert not decision.eligible
