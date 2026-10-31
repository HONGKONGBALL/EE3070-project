from cane.arbiter import OutputArbiter
from cane.model import Direction, OutputRequest, Priority


def make(audio, clock, haptic=None):
    return OutputArbiter(audio, haptic, safety_hold_s=2.0, repeat_cooldown_s=3.0, clock=clock)


def req(pid, prio, **kw):
    return OutputRequest(pid, prio, **kw)


def test_plays_when_idle(audio, clock):
    arb = make(audio, clock)
    assert arb.submit(req("turn_left", Priority.NAV, direction=Direction.LEFT))
    assert audio.played == [["turn_left"]]


def test_safety_preempts_nav(audio, clock):
    arb = make(audio, clock)
    arb.submit(req("go_straight", Priority.NAV))
    arb.submit(req("obstacle_front", Priority.SAFETY))
    assert audio.stopped == 1
    assert audio.played[-1] == ["obstacle_front"]


def test_nav_does_not_preempt_safety_and_waits_for_hold(audio, clock):
    arb = make(audio, clock)
    arb.submit(req("obstacle_front", Priority.SAFETY))
    arb.submit(req("go_straight", Priority.NAV))
    assert audio.played == [["obstacle_front"]]
    audio.finish()
    clock.advance(0.5)
    assert arb.tick() is None  # hold measured from end of the safety clip
    assert arb.safety_hold_active()
    clock.advance(1.0)
    assert arb.tick() is None
    clock.advance(1.1)
    played = arb.tick()
    assert played is not None and played.prompt_id == "go_straight"


def test_safety_order_before_nav_in_queue(audio, clock):
    arb = make(audio, clock)
    arb.submit(req("boot_ready", Priority.SYSTEM))  # occupies speaker
    arb.submit(req("turn_left", Priority.NAV, direction=Direction.LEFT))
    arb.submit(req("obstacle_head", Priority.SAFETY))
    # safety preempted the SYSTEM clip
    assert audio.played[-1] == ["obstacle_head"]
    assert arb.pending() == ["boot_ready", "turn_left"]  # interrupted clip re-queued


def test_critical_preempts_safety(audio, clock):
    arb = make(audio, clock)
    arb.submit(req("obstacle_front", Priority.SAFETY))
    arb.submit(req("stop_now", Priority.CRITICAL))
    assert audio.played[-1] == ["stop_now"]


def test_same_level_safety_does_not_preempt(audio, clock):
    arb = make(audio, clock)
    arb.submit(req("obstacle_front", Priority.SAFETY))
    arb.submit(req("obstacle_head", Priority.SAFETY))
    assert audio.stopped == 0
    audio.finish()
    arb.tick()
    assert audio.played[-1] == ["obstacle_head"]


def test_cooldown(audio, clock):
    arb = make(audio, clock)
    arb.submit(req("obstacle_front", Priority.SAFETY))
    audio.finish()
    clock.advance(1.0)
    assert not arb.submit(req("obstacle_front", Priority.SAFETY))
    clock.advance(2.5)
    assert arb.submit(req("obstacle_front", Priority.SAFETY))


def test_left_right_dropped_when_orientation_uncertain(audio, clock):
    arb = make(audio, clock)
    arb.set_orientation_ok(False)
    assert not arb.submit(req("turn_left", Priority.NAV, direction=Direction.LEFT))
    assert audio.played == [["nav_stop_align"]]


def test_orientation_lost_while_queued(audio, clock):
    arb = make(audio, clock)
    arb.submit(req("boot_ready", Priority.SYSTEM))
    arb.submit(req("turn_right", Priority.NAV, direction=Direction.RIGHT))
    arb.set_orientation_ok(False)
    audio.finish()
    arb.tick()
    assert ["turn_right"] not in audio.played


def test_stale_requests_expire(audio, clock):
    arb = make(audio, clock)
    arb.submit(req("obstacle_front", Priority.SAFETY))
    arb.submit(req("go_straight", Priority.NAV, ttl_s=1.0))
    audio.finish()
    clock.advance(5.0)
    arb.tick()
    assert ["go_straight"] not in audio.played


def test_vibration_sent_with_prompt(audio, clock):
    sent = []
    arb = make(audio, clock, haptic=sent.append)
    arb.submit(req("stop_now", Priority.CRITICAL, vibration="stop"))
    assert sent == ["stop"]


def test_no_public_way_to_clear_safety_hold():
    names = [n for n in dir(OutputArbiter) if not n.startswith("_")]
    assert not any("clear" in n or "override" in n or "veto" in n for n in names)


def test_optional_ordinary_spacing_is_global_but_does_not_delay_critical(audio,clock):
    arb=OutputArbiter(audio,clock=clock,ordinary_interval_s=3)
    assert arb.submit(req('puddle',Priority.SAFETY))
    audio.finish()
    clock.advance(.2)
    assert arb.submit(req('chair',Priority.SAFETY,ttl_s=5))
    assert audio.played==[['puddle']]
    clock.advance(2.8)
    arb.tick()
    assert audio.played[-1]==['chair']
    clock.advance(.1)
    assert arb.submit(req('drop',Priority.CRITICAL))
    assert audio.played[-1]==['drop'] and audio.stopped==1


def test_pending_ordinary_event_expires_in_spacing_window(audio,clock):
    arb=OutputArbiter(audio,clock=clock,ordinary_interval_s=3)
    arb.submit(req('puddle',Priority.SAFETY))
    audio.finish()
    clock.advance(.1)
    arb.submit(req('chair',Priority.SAFETY,ttl_s=1.5))
    clock.advance(3)
    arb.tick()
    assert audio.played==[['puddle']] and not arb.pending()
