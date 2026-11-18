"""Safety controls cover cancellation races and honest observation limits."""

from cane.nav.navigator import NavOutput
from cane.pico_link import PowerEvent
from cane.state_machine import State

from test_app import feed, flush
from test_nav import A

pytest_plugins = ("test_app",)


def test_critical_supply_prevents_light_reenable(app, clock):
    # Given critically low supply voltage.
    app.pico.inbox.put(PowerEvent(clock(), 4.1))
    feed(app, clock)
    # When a user asks to turn the light on.
    app.enqueue_control("light")
    feed(app, clock)
    # Then the critical load-shedding policy remains effective.
    assert [message for message in app.pico.sent if message.get("t") == "l"][-1]["v"] == 0.0


def test_all_invalid_ranges_are_a_visible_sensor_fault(app, audio, clock):
    # Given a live serial link whose distance channels all fail.
    # When three new observations carry no valid range.
    for _ in range(3):
        feed(app, clock, tf=None)
    # Then heartbeat traffic does not masquerade as working obstacle sensors.
    assert app.sm.state is State.FAULT
    assert "range_sensors_invalid" in app.status()["fault_reasons"]
    assert ["sensor_fault"] in audio.played


def test_cancel_queued_while_route_result_arrives_cannot_start_navigation(app, clock):
    # Given a route request whose result is queued for the next loop tick.
    feed(app, clock)
    app.on_location(*A, 5.0)
    feed(app, clock)
    app.on_destination_text("城大")
    app.on_button("ok", "short")
    # When cancel arrives before that tick executes.
    app.enqueue_control("cancel_destination")
    feed(app, clock)
    # Then the late route result leaves neither active nor pending navigation.
    assert not app.navigator.active
    assert not app.status()["nav"]["route_pending"]


def test_fault_recovery_retains_user_pause(app, clock):
    # Given a user pause followed by lost sensor data.
    feed(app, clock)
    app.enqueue_control("pause")
    feed(app, clock)
    clock.advance(2.0)
    app.step()
    # When a new sensor frame restores the link.
    feed(app, clock)
    # Then recovery does not silently resume the user's route.
    assert app.sm.state is State.PAUSED
    assert app.status()["paused"]


def test_resume_after_clear_hold_requires_new_alignment(app, audio, clock):
    # Given a paused cane whose obstacle has cleared and safety hold has elapsed.
    feed(app, clock, front=0.4)
    app.enqueue_control("pause")
    feed(app, clock)
    flush(app, audio, clock, secs=4.0)
    # When the user explicitly resumes while stationary.
    app.enqueue_control("resume")
    feed(app, clock)
    # Then navigation may continue only after alignment is confirmed again.
    assert app.sm.state is State.STOPPED
    assert not app.sm.turns_allowed


def test_repeat_drops_obsolete_route_output(app, audio, clock):
    # Given a last instruction from an old navigation version.
    feed(app, clock)
    app.on_location(*A, 5.0)
    feed(app, clock)
    app.last_nav = NavOutput("prompt", "go_straight", route_version=-1)
    app._last_nav_t = clock()
    # When the user asks to repeat it.
    app.enqueue_control("repeat")
    feed(app, clock)
    # Then no stale movement instruction is spoken or queued.
    assert ["go_straight"] not in audio.played
    assert "go_straight" not in app.arbiter.pending()


def test_unknown_text_cancels_previous_destination_confirmation(app, clock):
    # Given a destination waiting for confirmation.
    app.on_destination_text("城大")
    # When a subsequent utterance cannot be understood.
    result = app.on_destination_text("banana parking moon")
    # Then a later OK cannot accidentally choose the earlier destination.
    assert result["matched"] is None
    assert app.pending_dest is None


def test_missing_motion_observation_cannot_establish_alignment(app, clock):
    # Given fresh range data without a motion observer.
    feed(app, clock)
    app.imu = None
    # When the user presses confirm.
    app.enqueue_control("confirm")
    feed(app, clock)
    # Then the system does not fabricate a stationary orientation reference.
    assert not app.sm.aligned
    assert not app.arbiter.orientation_ok


def test_expired_location_rejects_queued_route_and_releases_pending(app, clock):
    # Given a completed route result waiting for the app loop.
    feed(app, clock)
    app.on_location(*A, 5.0)
    feed(app, clock)
    app.on_destination_text("城大")
    app.on_button("ok", "short")
    # When time passes beyond the location validity window before applying it.
    clock.advance(16.0)
    feed(app, clock)
    # Then the old result cannot restart route guidance.
    assert not app.navigator.active
    assert not app.status()["nav"]["route_pending"]


def test_output_dispatch_is_written_to_persistent_event_log(app, clock):
    # Given a fresh close obstacle.
    # When the safety arbiter dispatches its stop warning.
    feed(app, clock, front=0.4)
    # Then the log distinguishes actual playback from request submission.
    outputs = [event for event in app.logbook.recent if event["kind"] == "output"]
    assert any(event["action"] == "played" and event["prompt_id"] == "stop_now" for event in outputs)


def test_fault_during_crossing_preserves_manual_confirmation_gate(app, clock):
    # Given a street-crossing hand-off waiting for the user's confirmation.
    feed(app, clock)
    app.sm.handle("crossing_detected")
    clock.advance(2.0)
    app.step()
    # When sensor data returns.
    feed(app, clock)
    # Then recovery cannot quietly replace the crossing hand-off with READY.
    assert app.sm.state is State.CROSSING
    assert not app.sm.nav_allowed


def test_cantonese_output_does_not_claim_a_cantonese_asr_model(app):
    # Given Cantonese prompts with the optional Vosk input backend enabled.
    app.lang = "yue"
    app.cfg.voice.backend = "vosk"
    # When the app reports microphone recognition availability.
    reason = app._voice_unavailable_reason()
    # Then Mandarin fallback is never advertised as verified Cantonese ASR.
    assert reason == "cantonese_asr_unavailable"
