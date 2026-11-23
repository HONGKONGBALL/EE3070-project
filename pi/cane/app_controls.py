"""Thread-safe user control boundary and safety-checked main-loop actions."""

from __future__ import annotations

import queue
from typing import assert_never

from .app_context import AppContext
from .app_types import ControlAction, ControlResult, QueuedControl
from .config import LANGUAGES
from .model import OutputRequest, Priority
from .power import PowerStage
from .state_machine import State

class ControlsMixin:
    def enqueue_control(self: AppContext, action: str) -> ControlResult:
        """Accept a known action for execution by the next safety-loop tick."""
        app = self
        try:
            parsed = ControlAction(action)
        except ValueError:
            return {"accepted": False, "action": action, "error": "unknown_action"}
        with app._state_lock:
            if parsed is ControlAction.CONFIRM:
                crossing = app.sm.state is State.CROSSING
                if not crossing and app.destination_search.state in {"searching", "results", "resolving"}:
                    return {"accepted": False, "action": action, "error": "destination_choice_pending"}
                queued = QueuedControl(parsed, app.pending_dest, crossing, app.destination_search.id)
            else:
                queued = QueuedControl(parsed)
            try:
                app._controls.put_nowait(queued)
            except queue.Full:
                return {"accepted": False, "action": action, "error": "control_queue_full"}
        return {"accepted": True, "action": action}

    def press_button(self: AppContext, button: str, kind: str) -> dict[str, object]:
        """Queue a key press for the main loop, the way one from the Pico arrives."""
        app = self
        app.logbook.write("button", button=button, ev=kind, source="diagnostics")
        app._jobs.put(lambda: app.on_button(button, kind))
        return {"accepted": True, "button": button, "kind": kind}

    def _drain_controls(self: AppContext) -> None:
        app = self
        for _ in range(32):
            try:
                control = app._controls.get_nowait()
            except queue.Empty:
                return
            if control.action is ControlAction.CONFIRM and (
                control.crossing != (app.sm.state is State.CROSSING)
                or (not control.crossing and (
                    control.destination is not app.pending_dest
                    or control.search_id != app.destination_search.id
                ))
            ):
                continue
            app._execute_control(control.action)

    def _execute_control(self: AppContext, action: ControlAction) -> None:
        """Safety and sensor processing precede queued controls in step()."""
        app = self
        reason = ""
        match action:
            case ControlAction.CONFIRM:
                app._confirm()
            case ControlAction.CANCEL:
                app.stop_navigation(announce=False)
                app.say("dest_cancelled", Priority.SYSTEM)
            case ControlAction.PAUSE:
                app._pause()
            case ControlAction.RESUME:
                reasons = app._resume_blocked_reasons()
                if reasons:
                    reason = ",".join(reasons)
                    app.say("resume_blocked", Priority.SYSTEM)
                elif app.sm.handle("resume"):
                    app.help_requested = False
                    app.last_nav = None
                    app.say("nav_resumed", Priority.SYSTEM)
            case ControlAction.HELP:
                app.help_requested = True
                app._pause(announce=False)
                app.pico.buzz("beep")
                app.say("help_local", Priority.SYSTEM)
                app.logbook.write("help", delivery="local_only", remote_sent=False)
            case ControlAction.REPEAT:
                app.repeat_last_nav()
            case ControlAction.LANGUAGE:
                app.cycle_language()
            case ControlAction.LIGHT:
                app._light_on = not app._light_on and app.power.stage is not PowerStage.CRITICAL
                app.pico.light((0.2 if app.power.reduced else 0.8) if app._light_on else 0.0)
            case ControlAction.TEST_MODE:
                app.test_mode = not app.test_mode
                app.sm.test_mode = app.test_mode
                if app.test_mode:
                    app.say("test_mode_on")
                app.logbook.write("test_mode", on=app.test_mode)
            case ControlAction.SCAN:
                if not app._start_stereo_scan():
                    reason = "scan_unavailable_or_not_stationary"
                    app.say("scan_unavailable", Priority.SYSTEM)
            case ControlAction.STOP_NAVIGATION:
                app.stop_navigation()
            case ControlAction.VOICE_INPUT:
                app.start_voice_input()
            case ControlAction.VOICE_STOP:
                app.stop_voice_input()
            case ControlAction.WAKE_ENABLE:
                app.cfg.voice.wake_enabled = True
            case ControlAction.WAKE_DISABLE:
                app.cfg.voice.wake_enabled = False
                app.voice_wake.pause()
            case unreachable:
                assert_never(unreachable)
        app.last_control = {"action": action.value, "executed": True,
                             "blocked_reason": reason, "time": app.clock()}
        app.logbook.write("control", **app.last_control)

    def _pause(self: AppContext, announce: bool = True) -> None:
        app = self
        app._cancel_pending_route_for_pause()
        app.sm.handle("pause")
        app._route_generation += 1
        app._route_pending = False
        app.last_nav = None
        app._scan_generation += 1
        app.arbiter.discard_navigation()
        if announce:
            app.say("nav_paused", Priority.SYSTEM)

    def _resume_blocked_reasons(self: AppContext) -> list[str]:
        app = self
        reasons = sorted(app.fault_reasons)
        if not app._pico_ok:
            reasons.append("fresh_sensors_required")
        if app.last_hazards:
            reasons.append("hazard_present")
        if app.arbiter.safety_hold_active():
            reasons.append("safety_hold_active")
        if app.imu is None:
            reasons.append("stationary_observation_unavailable")
        elif app.imu.moving:
            reasons.append("stop_before_resume")
        if app.power.stage is PowerStage.CRITICAL:
            reasons.append("critical_supply_voltage")
        return reasons

    def _confirm(self: AppContext) -> None:
        app = self
        if app.sm.paused or app.sm.state is State.FAULT:
            app.say("resume_blocked", Priority.SYSTEM)
            return
        if app.last_hazards or app.arbiter.safety_hold_active() or not app._pico_ok:
            app.say("resume_blocked", Priority.SYSTEM)
            return
        if app.sm.state is State.CROSSING:
            app._handle_nav_outputs(app.navigator.confirm_crossing())
            app.sm.handle("crossing_confirmed")
        elif app.pending_dest is not None:
            destination, app.pending_dest = app.pending_dest, None
            app.start_navigation(destination)
        else:
            if app.sm.state.stationary and app.imu is not None and not app.imu.moving:
                app.sm.handle("aligned")
                if app.vision is not None and app.vision.heading is not None:
                    app.vision.heading.reset_alignment()
            app.repeat_last_nav()

    def on_button(self: AppContext, button: str, kind: str) -> None:
        """Physical buttons enter on the main loop; legacy direct calls remain."""
        app = self
        action = {
            ("ok", "short"): ControlAction.CONFIRM,
            ("ok", "long"): ControlAction.HELP,
            ("dest", "long"): ControlAction.STOP_NAVIGATION,
            ("repeat", "short"): ControlAction.REPEAT,
            ("repeat", "long"): ControlAction.LANGUAGE,
            ("mode", "short"): ControlAction.LIGHT,
            ("mode", "long"): ControlAction.TEST_MODE,
        }.get((button, kind))
        if action is not None:
            app._execute_control(action)
        elif (button, kind) == ("dest", "short"):
            if app.cfg.voice.manual_stop and app.listening:
                app.stop_voice_input()
            else:
                app.start_voice_input()

    def cycle_language(self: AppContext) -> None:
        app = self
        app.lang = LANGUAGES[(LANGUAGES.index(app.lang) + 1) % len(LANGUAGES)]
        if hasattr(app.audio, "lang"):
            setattr(app.audio, "lang", app.lang)
        app.say("lang_changed")

    def repeat_last_nav(self: AppContext) -> None:
        """Repeat only a current route's fresh instruction, subject to safety."""
        app = self
        output = app.last_nav
        valid = (output is not None and app.navigator.is_current(output)
                 and app.sm.nav_allowed and app._pico_ok
                 and app._location_fresh() and app.clock() - app._last_nav_t <= 15.0)
        if not valid or app.last_hazards or app.arbiter.safety_hold_active():
            app.say("nav_nothing_to_repeat", Priority.SYSTEM)
            return
        if output is not None:
            app.arbiter.allow_repeat(OutputRequest(
                output.prompt_id, Priority.NAV, extra_clips=output.extra_clips, text=output.text))
            app.say(output.prompt_id, extra=output.extra_clips,
                     direction=output.direction, text=output.text)
