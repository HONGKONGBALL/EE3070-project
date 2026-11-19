"""Fresh-observation safety, motion, terrain and stationary camera scans."""

from __future__ import annotations

import math
from typing import assert_never

from .app_context import AppContext
from .model import ButtonEvent, SensorFrame
from .pico_link import PicoMessage, PicoWarning, PowerEvent
from .power import PowerStage
from .state_machine import State
from .terrain import VibrationFeatures, vibration_features

from .vision.stereo import ScanResult


class SensingMixin:
    def _on_pico(self: AppContext, message: PicoMessage) -> None:
        app = self
        match message:
            case SensorFrame():
                age = app.clock() - message.t
                if not math.isfinite(age) or age < 0 or age > app.cfg.pico.timeout_s:
                    app.logbook.write("observation_discarded", reason="invalid_or_stale_sensor_time")
                    return
                if app.frame is not None and message.t <= app.frame.t:
                    return
                app.frame = message
                if message.tfmini_m is not None and math.isfinite(message.tfmini_m):
                    app.tf_window.append(message.tfmini_m)
            case ButtonEvent():
                app.logbook.write("button", button=message.button, ev=message.kind)
                app._jobs.put(lambda button=message.button, kind=message.kind:
                              app.on_button(button, kind))
            case PowerEvent():
                app._on_power(message.vsys)
            case PicoWarning():
                app.logbook.write("pico_warning", msg=message.msg)
            case unreachable:
                assert_never(unreachable)

    def _check_pico(self: AppContext, now: float) -> None:
        app = self
        fresh = app.frame is not None and 0 <= now - app.frame.t <= app.cfg.pico.timeout_s
        if fresh:
            app._pico_ok = True
            app.fault_reasons.discard("pico_timeout")
            if app.sm.state is State.FAULT and not app.fault_reasons:
                app.sm.handle("recovered")
                app.say("sensor_ok")
        elif app._pico_ok or now - app._started_at > app.cfg.pico.timeout_s:
            app._pico_ok = False
            app.last_hazards = []
            app.terrain_class = "unknown"
            app.last_scan = None
            app.tf_window.clear()
            app.hazards.reset()
            app._invalid_range_frames = 0
            app.last_nav = None
            app._scan_generation += 1
            app.arbiter.discard_navigation()
            app.arbiter.discard_obsolete_hazards(set())
            if "pico_timeout" not in app.fault_reasons:
                app.fault_reasons.add("pico_timeout")
                app.sm.handle("fault")
                app.say("pico_lost")
                app.logbook.write("fault", reason="pico_timeout")

    def _update_motion(self: AppContext) -> None:
        app = self
        if app.imu is None:
            app.sm.handle("orientation_lost")
            return
        moving = bool(app.imu.moving)
        if moving and app.sm.state in (State.READY, State.STOPPED):
            app.sm.handle("motion_start")
            app.last_scan = None
        elif not moving and app.sm.state is State.WALKING:
            app.sm.handle("motion_stop")
        if (app.sm.state is State.STOPPED and app.sm.entered_at != app._last_scan_entered
                and app.sm.time_in_state() >= 1.0):
            app._last_scan_entered = app.sm.entered_at
            app._start_stereo_scan()
        if app.vision is not None:
            app.vision.walking = app.sm.state is State.WALKING and not app.power.reduced
            heading = app.vision.heading
            if heading is not None and app.sm.state is State.WALKING and not heading.orientation_ok:
                app.sm.handle("orientation_lost")
                app.arbiter.discard_navigation()
                app.last_nav = None

    def roughness_tick(self: AppContext) -> None:
        """IMU thread computes a window, then queues its timestamped result."""
        app = self
        if app.imu is None or app.power.reduced:
            return
        window = app.imu.window()
        if len(window) < 16:
            return
        features = vibration_features(window, app.cfg.imu.rate_hz, list(app.tf_window))
        classification = app.roughness.classify(features, app.sm.state is State.WALKING)
        timestamp = app.clock()
        app._jobs.put(lambda: app._on_terrain(classification, features, timestamp))

    def _on_terrain(self: AppContext, classification: str, features: VibrationFeatures,
                    timestamp: float | None = None) -> None:
        app = self
        if timestamp is not None and app.clock() - timestamp > app.cfg.imu.window_s:
            return
        if not app._pico_ok or app.sm.state is not State.WALKING:
            return
        if classification != app.terrain_class:
            app.logbook.write("terrain", cls=classification, rms=round(features.rms, 3),
                               tf_std=round(features.tf_std, 3))
        app.terrain_class = classification
        if classification in ("rough", "soft"):
            app.say("rough_ground" if classification == "rough" else "soft_ground")

    def _run_hazards(self: AppContext) -> None:
        app = self
        frame = app.frame
        if not app._pico_ok or frame is None or frame.t == app._last_assessed_t:
            return
        app._last_assessed_t = frame.t
        if not app._check_range_health(frame):
            app.last_hazards = []
            app.arbiter.discard_obsolete_hazards(set())
            return
        # Pausing navigation never disables hazards. Without observed motion,
        # assume motion for warnings rather than claiming the person is still.
        moving = app.imu.moving if app.imu is not None else True
        tilt = app.imu.pitch_deg if app.imu is not None else None
        found = app.hazards.assess(frame, moving=moving, tilt_deg=tilt)
        app.last_hazards = [hazard.kind for hazard in found]
        app.arbiter.discard_obsolete_hazards({hazard.prompt_id for hazard in found})
        if found:
            app.last_nav = None
            app.arbiter.discard_navigation()
        for hazard in found:
            if app.say(hazard.prompt_id):
                app.logbook.write("hazard", hazard=hazard.kind, d=hazard.distance_m)

    def _check_range_health(self: AppContext, frame: SensorFrame) -> bool:
        """A live serial heartbeat is insufficient when every ranger has failed."""
        app = self
        usable = (app.hazards.valid_distance(frame.front_m) is not None
                  or app.hazards.valid_distance(frame.up_m) is not None
                  or (app.hazards.valid_distance(frame.tfmini_m) is not None
                      and frame.tfmini_strength >= app.cfg.tfmini.min_strength))
        app._invalid_range_frames = 0 if usable else app._invalid_range_frames + 1
        if app._invalid_range_frames >= 3:
            if "range_sensors_invalid" not in app.fault_reasons:
                app.fault_reasons.add("range_sensors_invalid")
                app.sm.handle("fault")
                app.last_hazards, app.last_nav = [], None
                app.arbiter.discard_navigation()
                app.say("sensor_fault")
                app.logbook.write("fault", reason="range_sensors_invalid")
            return False
        if usable and "range_sensors_invalid" in app.fault_reasons:
            app.fault_reasons.remove("range_sensors_invalid")
            if not app.fault_reasons:
                app.sm.handle("recovered")
                app.say("sensor_ok")
        return usable

    def _start_stereo_scan(self: AppContext) -> bool:
        app = self
        if (app.vision is None or app.imu is None or app.imu.moving or not app._pico_ok
                or not app.sm.state.stationary or app.power.reduced):
            return False
        pitch = app.imu.pitch_deg
        if pitch is None:
            return False  # a zero pitch would invent a camera/ground orientation
        app._scan_generation += 1
        generation, timestamp = app._scan_generation, app.clock()
        app.vision.request_scan(pitch, lambda result: app._jobs.put(
            lambda: app._on_scan(result, generation, timestamp)))
        return True

    def _on_scan(self: AppContext, result: ScanResult, generation: int | None = None,
                 timestamp: float | None = None) -> None:
        app = self
        if (not app.sm.state.stationary or not app._pico_ok or app.power.reduced
                or app.imu is None or app.imu.moving or app.imu.pitch_deg is None):
            return
        if ((generation is not None and generation != app._scan_generation)
                or (timestamp is not None and app.clock() - timestamp > 5.0)):
            return
        app.logbook.write("scan", prompts=result.prompts, uphill=round(result.uphill_deg, 1),
                           left_down=round(result.left_down_deg, 1), step_d=result.step_distance_m)
        app.last_scan = {"uphill_deg": result.uphill_deg, "left_down_deg": result.left_down_deg,
                          "step_distance_m": result.step_distance_m}
        app._last_scan_t = app.clock()
        for prompt_id in result.prompts:
            app.say(prompt_id)

    def _on_power(self: AppContext, voltage: float) -> None:
        app = self
        changed = app.power.update(voltage)
        app.vsys = app.power.voltage
        if not changed:
            return
        stage = app.power.stage
        app.logbook.write("power", stage=stage.value, vsys_v=voltage, calibrated=False)
        match stage:
            case PowerStage.UNKNOWN:
                return
            case PowerStage.NOMINAL:
                app.say("power_restored")
            case PowerStage.CAUTION:
                app.say("power_caution")
            case PowerStage.REDUCED:
                app.pico.light(0.2 if app._light_on else 0.0)
                app._scan_generation += 1
                app.say("power_reduced")
            case PowerStage.CRITICAL:
                app.pico.light(0.0)
                app._light_on = False
                app._pause(announce=False)
                app.say("power_critical")
            case unreachable:
                assert_never(unreachable)
