"""Physical buttons obey the same current-observation safety gate as the UI."""

import pytest

from cane.model import ButtonEvent, SensorFrame
from cane.state_machine import State

from test_app import GROUND, feed

pytest_plugins = ("test_app",)


@pytest.mark.parametrize("button_first", [False, True])
def test_same_batch_obstacle_prevents_physical_crossing_confirmation(app, clock, button_first):
    # Given a crossing awaiting confirmation with a previously clear frame.
    feed(app, clock)
    app.sm.handle("crossing_detected")
    app.navigator.waiting_crossing = 1
    clock.advance(0.05)
    frame = SensorFrame(clock(), front_m=0.4, tfmini_m=GROUND, tfmini_strength=900)
    button = ButtonEvent(clock(), "ok", "short")
    messages = (button, frame) if button_first else (frame, button)
    for message in messages:
        app.pico.inbox.put(message)
    # When the loop handles a new close obstacle and the physical OK together.
    app.step()
    # Then safety is assessed before confirmation regardless of inbox order.
    assert app.sm.state is State.CROSSING
    assert app.navigator.waiting_crossing == 1
    assert app.last_hazards == ["stop"]


def test_physical_confirmation_still_works_after_a_clear_observation(app, clock):
    # Given fresh clear range data and an observed stationary cane.
    feed(app, clock)
    clock.advance(0.05)
    app.pico.inbox.put(SensorFrame(clock(), front_m=3.0, tfmini_m=GROUND, tfmini_strength=900))
    app.pico.inbox.put(ButtonEvent(clock(), "ok", "short"))
    # When physical OK is processed after this tick's safety assessment.
    app.step()
    # Then ordinary stationary alignment remains available.
    assert app.sm.aligned
