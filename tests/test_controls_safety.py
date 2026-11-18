"""User controls retain the safety gate and reject obsolete navigation."""

from concurrent.futures import ThreadPoolExecutor

from cane.pico_link import PowerEvent
from cane.state_machine import State

from test_app import feed

pytest_plugins = ("test_app",)


def test_pause_control_is_queued_and_keeps_obstacle_safety(app, audio, clock):
    # Given a running cane and a fresh sensor sample.
    feed(app, clock)
    # When the HTTP thread requests a pause.
    result = app.enqueue_control("pause")
    assert app.sm.state is State.READY
    feed(app, clock, front=0.4)
    # Then execution is serialized and safety still interrupts.
    assert result["accepted"] and app.sm.state is State.PAUSED
    assert ["stop_now"] in audio.played


def test_resume_is_blocked_by_current_hazard(app, clock):
    # Given a paused cane with an obstacle still present.
    feed(app, clock, front=0.4)
    app.enqueue_control("pause")
    feed(app, clock, front=0.4)
    # When the user requests resume.
    app.enqueue_control("resume")
    feed(app, clock, front=0.4)
    # Then the cane stays paused with an actionable reason.
    assert app.sm.state is State.PAUSED
    assert "hazard_present" in app.status()["resume_blocked_reasons"]


def test_help_is_local_and_never_claims_remote_delivery(app, clock):
    # Given the local Mac host with no messaging adapter.
    feed(app, clock)
    # When help is requested.
    app.enqueue_control("help")
    feed(app, clock)
    # Then status records a local request, not a sent emergency message.
    status = app.status()
    assert status["help_requested"]
    assert status["help_delivery"] == "local_only"
    assert status["paused"]
    assert not status["capabilities"]["remote_help"]


def test_initial_absence_of_pico_eventually_faults(app, audio, clock):
    # Given startup without any Pico frame.
    # When the first-frame timeout expires.
    clock.advance(app.cfg.pico.timeout_s + 0.1)
    app.step()
    # Then missing startup hardware is visible and audible.
    assert app.sm.state is State.FAULT
    assert "pico_timeout" in app.status()["fault_reasons"]
    assert ["pico_lost"] in audio.played


def test_old_frame_cannot_satisfy_hazard_debounce(app, audio, clock):
    # Given one head obstacle frame, insufficient for confirmation.
    feed(app, clock, up=1.0)
    # When the loop polls again without a new sensor frame.
    app.step()
    # Then the old frame cannot act as a second observation.
    assert ["obstacle_head"] not in audio.played


def test_sensor_timeout_removes_old_hazards_and_pending_nav(app, audio, clock):
    # Given a known obstacle and a navigation request behind it.
    feed(app, clock, front=0.4)
    app.say("go_straight")
    # When the sensor source expires.
    clock.advance(2.0)
    app.step()
    # Then stale hazards and obsolete directions are removed.
    assert app.last_hazards == []
    assert "go_straight" not in app.arbiter.pending()


def test_power_reports_supply_risk_without_fake_battery_estimates(app, clock):
    # Given a low VSYS voltage (not a battery gauge).
    app.pico.inbox.put(PowerEvent(clock(), 4.1))
    # When the main loop processes the power measurement.
    feed(app, clock)
    # Then the critical supply stage pauses navigation and dims the light.
    status = app.status()
    assert status["power"]["stage"] == "critical"
    assert status["power"]["battery_percent"] is None
    assert status["power"]["remaining_minutes"] is None
    assert status["paused"]
    assert {"t": "l", "v": 0.0} in app.pico.sent


def test_cancel_destination_removes_pending_confirmation(app, audio, clock):
    # Given a pending destination readback.
    feed(app, clock)
    app.on_destination_text("城大")
    # When cancel is submitted by the UI.
    app.enqueue_control("cancel_destination")
    feed(app, clock)
    # Then OK no longer starts that destination and its readback is gone.
    assert app.pending_dest is None
    assert "dest_dest1" not in app.arbiter.pending()


def test_unknown_control_is_rejected_at_boundary(app):
    # Given a caller outside the core loop.
    # When it sends an unsupported control.
    result = app.enqueue_control("reboot_everything")
    # Then it gets an explicit error without mutating app state.
    assert result == {"accepted": False, "action": "reboot_everything", "error": "unknown_action"}


def test_concurrent_controls_are_serialized_without_loss(app, clock):
    # Given concurrent browser control requests.
    with ThreadPoolExecutor(max_workers=4) as pool:
        # When each submits a language cycle.
        results = list(pool.map(app.enqueue_control, ["language"] * 12))
    feed(app, clock)
    # Then all requests were accepted and executed once.
    assert all(result["accepted"] for result in results)
    assert app.lang == "cmn"
    assert sum(event["kind"] == "control" for event in app.logbook.recent) == 12
