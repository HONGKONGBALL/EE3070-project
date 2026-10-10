from cane.state_machine import CaneStateMachine, State


def test_normal_cycle(clock):
    sm = CaneStateMachine(clock)
    assert sm.state is State.BOOT
    assert sm.handle("boot_done")
    assert sm.handle("motion_start") and sm.state is State.WALKING
    assert sm.handle("motion_stop") and sm.state is State.STOPPED
    assert sm.handle("motion_start") and sm.state is State.WALKING


def test_invalid_event_ignored(clock):
    sm = CaneStateMachine(clock)
    assert not sm.handle("motion_start")
    assert sm.state is State.BOOT


def test_alignment_only_when_stationary(clock):
    sm = CaneStateMachine(clock)
    sm.handle("boot_done")
    sm.handle("motion_start")
    assert not sm.handle("aligned")
    sm.handle("motion_stop")
    assert sm.handle("aligned") and sm.turns_allowed
    sm.handle("motion_start")
    assert sm.turns_allowed  # walking straight after aligning is fine
    sm.handle("orientation_lost")
    assert not sm.turns_allowed


def test_crossing_handoff_requires_confirmation(clock):
    sm = CaneStateMachine(clock)
    sm.handle("boot_done")
    sm.handle("motion_stop")  # ignored in READY
    sm.handle("aligned")
    sm.handle("crossing_detected")
    assert sm.state is State.CROSSING
    assert not sm.nav_allowed and not sm.aligned
    assert not sm.handle("motion_start")  # walking does not leave CROSSING
    assert sm.handle("crossing_confirmed") and sm.state is State.STOPPED


def test_fault_from_any_state_and_recover(clock):
    for setup in ([], ["boot_done"], ["boot_done", "motion_start"]):
        sm = CaneStateMachine(clock)
        for e in setup:
            sm.handle(e)
        assert sm.handle("fault") and sm.state is State.FAULT
        assert not sm.handle("fault")
        assert sm.handle("recovered") and sm.state is State.READY


def test_listener(clock):
    sm = CaneStateMachine(clock)
    seen = []
    sm.on_transition(lambda tr: seen.append((tr.src, tr.dst)))
    sm.handle("boot_done")
    assert seen == [(State.BOOT, State.READY)]
