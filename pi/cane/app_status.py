"""Read-only, secret-free snapshots with explicit capability limitations."""

from __future__ import annotations

import time
from dataclasses import asdict

from .app_context import AppContext
from .app_types import SearchOriginView, Status


class StatusMixin:
    def status(self: AppContext) -> Status:
        """Lock the snapshot so the web cannot mix different control ticks."""
        app = self
        with app._state_lock:
            frame, position = app.frame, app.navigator.position
            heading = app.vision.heading if app.vision is not None else None
            voice_reason = app._voice_unavailable_reason()
            search_origin: SearchOriginView | None = None
            if app.nearby_search:
                if app.manual_search_origin is not None:
                    search_origin = {"kind": "manual", "name": app.manual_search_origin.name}
                elif app._location_fresh():
                    search_origin = {"kind": "iphone"}
            valid_front = frame is not None and app.hazards.valid_distance(frame.front_m) is not None
            valid_head = frame is not None and app.hazards.valid_distance(frame.up_m) is not None
            valid_ground = (frame is not None and app.cfg.tfmini.role == "ground"
                            and app.hazards.valid_distance(frame.tfmini_m) is not None
                            and frame.tfmini_strength >= app.cfg.tfmini.min_strength)
            return {
                "time": time.time(), "state": app.sm.state.value,
                "aligned": app.sm.aligned, "paused": app.sm.paused,
                "help_requested": app.help_requested, "help_delivery": "local_only",
                "test_mode": app.test_mode, "sim": app.sim, "language": app.lang,
                "pico_ok": app._pico_ok, "vsys": app.vsys,
                "tfmini_role": app.cfg.tfmini.role,
                "sensors": None if frame is None else {
                    "front_m": frame.front_m, "up_m": frame.up_m, "tfmini_m": frame.tfmini_m,
                    "tfmini_strength": frame.tfmini_strength,
                    "age_s": round(app.clock() - frame.t, 2), "valid": app._pico_ok,
                },
                "imu": None if app.imu is None else {
                    "moving": app.imu.moving, "pitch_deg": app.imu.pitch_deg,
                    "scope": "cane_motion_only",
                },
                "terrain": app.terrain_class, "hazards": list(app.last_hazards),
                "scan": app.last_scan if app.clock() - app._last_scan_t <= 5.0 else None,
                "fault_reasons": sorted(app.fault_reasons),
                "resume_blocked_reasons": app._resume_blocked_reasons(),
                "safety_hold": app.arbiter.safety_hold_active(),
                "orientation_ok": app.arbiter.orientation_ok,
                "speaking": app.arbiter.current.prompt_id if app.arbiter.current else None,
                "queue": app.arbiter.pending(),
                "recent_outputs": [asdict(event) for event in list(app.arbiter.history)[-15:]],
                "nav": {
                    "active": app.navigator.active, "route_pending": app._route_pending,
                    "route_version": app.navigator.route_version,
                    "amap_key_configured": app.amap is not None,
                    "waiting_crossing": app.navigator.waiting_crossing is not None,
                    "step": None if position is None else position.step,
                    "to_step_end_m": None if position is None else round(position.to_step_end_m, 1),
                    "location_age_s": None if app.location is None else round(app.clock() - app.location[3], 1),
                    "location_valid": app._location_fresh(),
                    "location": None if app.destination_only or app.location is None else {
                        "lng": app.location[0], "lat": app.location[1], "accuracy_m": app.location[2],
                    },
                    "pending_destination": app.pending_dest.name if app.pending_dest else None,
                },
                "power": app.power.status(),
                "capabilities": {
                    "front_obstacle": app._pico_ok and valid_front,
                    "head_obstacle": app._pico_ok and valid_head,
                    "ground_range": app._pico_ok and valid_ground,
                    "cane_motion": app.imu is not None,
                    "body_motion_tracking": False, "automatic_obstacle_detour": False,
                    "remote_help": False, "battery_state_of_charge": False,
                    "slope": app.vision is not None and app.imu is not None
                             and app.imu.pitch_deg is not None and not app.power.reduced,
                    "heading_basis": "visual_odometry" if heading is not None else "user_confirmation",
                    "heading_requires_stationary_observation": True,
                    "missing": [name for name, present in (
                        ("front_distance", app._pico_ok and valid_front),
                        ("head_distance", app._pico_ok and valid_head),
                        ("ground_distance", app._pico_ok and valid_ground),
                        ("cane_motion", app.imu is not None),
                        ("calibrated_stereo_pitch", app.vision is not None and app.imu is not None
                         and app.imu.pitch_deg is not None),
                    ) if not present],
                },
                "voice": {**app.destination_search.snapshot(),
                          "available": voice_reason is None, "reason": voice_reason,
                          "listening": app.listening, "recording": app.voice_recording,
                          "manual_stop": app.cfg.voice.manual_stop,
                          "can_stop_recording": bool(app.voice_record_stop and not app.voice_record_stop.is_set()),
                          "text_input_available": True,
                          "cantonese_model": False, "wake_enabled": app.cfg.voice.wake_enabled,
                          "wake_phrase": app.cfg.voice.wake_phrase,
                          "wake_retrying": app.voice_wake.awaiting_retry(app.clock()),
                          "place_search_online": bool(app.cfg.voice.places_script or app.pi_places),
                          "place_search_source": app.cfg.voice.places_source,
                          "phone_location_ready": app.nearby_search and app._location_fresh(),
                          "search_origin": search_origin, "origin_search": app.origin_search.snapshot()},
                "last_control": app.last_control, "control_pending": app._controls.qsize(),
            }
