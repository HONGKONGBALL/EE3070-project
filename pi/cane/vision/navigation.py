"""Connect the Pi 5 navigation controller to the shared camera service."""

import logging
from pathlib import Path
import sqlite3
import threading
import time

from ..app import CaneApp
from ..config import load_config
from ..model import Priority, SensorFrame
from ..nav.amap import _urllib_fetch
from ..nav.providers import map_client
from ..prompts import destination_prompt_id
from ..voice.destinations import match_destination
from ..voice.pi_places import PiPlaceSearch
from ..web.pairing import LAN_NETWORKS, PHONE_PORT, PhonePair
from .distance import PicoDistanceProvider
from .voice_commands import NavigationReply


LOG = logging.getLogger(__name__)
# The cane has four keys and the firmware has no vision pin yet, so a short press of the
# fourth key (GP13, "mode") is K4. A long press still toggles test mode.
VISION_TAPS = {("vision", "short"), ("mode", "short")}
RANGE_WARNINGS = {"stop_now", "obstacle_front", "obstacle_head", "drop_ahead", "step_up_ahead"}
# A range warning seen this recently is the same obstacle, not a new one.
SAME_WARNING_S = 0.5


def measured_amap_fetch(budget, report):
    def fetch(url, timeout):
        started = time.monotonic()
        success = False
        try:
            if budget:
                try:
                    allowed = budget.reserve()
                except (OSError, sqlite3.Error):
                    allowed = False
                if not allowed:
                    raise OSError("Navigation acceptance budget exhausted")
            data = _urllib_fetch(url, timeout)
            success = True
            return data
        finally:
            report("navigation_request", provider="amap", success=success,
                   latency_s=round(time.monotonic()-started, 3))
    return fetch


class SharedCane(CaneApp):
    def __init__(self, cfg, distance, audio, amap):
        self.distance = distance
        self.cloud_assistant = None
        self._warning_seen = {}
        super().__init__(cfg, pico=distance.link, audio=audio, amap=amap,
                         pi_places=PiPlaceSearch(amap), output_arbiter=audio.arbiter)

    def say(self, prompt_id, priority=None, **options):
        if prompt_id in RANGE_WARNINGS and self.cloud_assistant:
            # Repeating "stop" every second would cut the cue, the recording and the answer of a K2 or
            # K4 turn the user asked for. During a turn, a warning for the same obstacle stays quiet.
            # A new kind of warning, or the same one after a gap, still interrupts.
            now = self.clock()
            seen = self._warning_seen.get(prompt_id, float("-inf"))
            self._warning_seen[prompt_id] = now
            if now - seen < SAME_WARNING_S and self.cloud_assistant.in_turn():
                return False
        return super().say(prompt_id, priority, **options)

    def _on_pico(self, message):
        if isinstance(message, SensorFrame):
            if not 0 <= self.clock() - message.t <= self.cfg.pico.timeout_s:
                return
            if not self.distance.accept(message):
                if self.distance.sample is None:
                    self.frame = None
                return
        super()._on_pico(message)

    def on_button(self, button, kind):
        if (button, kind) == ("dest", "short"):
            if self.cloud_assistant:
                self.cloud_assistant.toggle("navigation_button")
        elif (button, kind) in VISION_TAPS and self.cloud_assistant:
            self.cloud_assistant.toggle("vision_button")
        else:
            if (button, kind) == ("dest", "long") and self.cloud_assistant:
                self.cloud_assistant.cancel_navigation_recording()
                if self.cloud_assistant.dialogue is not None:
                    self.cloud_assistant.dialogue.reset()
            super().on_button(button, kind)

    def status(self):
        result = super().status()
        if self.cloud_assistant:
            with self.cloud_assistant.lock:
                listening = self.cloud_assistant.trigger == "navigation_button"
                recording = listening and self.cloud_assistant.state == "LISTENING_FOR_COMMAND"
            result["voice"].update(available=True, reason=None, listening=listening,
                                   recording=recording, manual_stop=True)
        return result

    def _stage_destination(self, destination):
        # Provider places have no pre-generated name clip. Safari shows the selected name.
        if any(preset == destination for preset in self.cfg.nav.destinations):
            clip = destination_prompt_id(destination.id) + ".wav"
            if (Path(self.cfg.audio.dir) / self.lang / clip).is_file():
                super()._stage_destination(destination)
                return
        self.pending_dest = destination
        self.arbiter.discard_information()
        self.say("dest_confirm", Priority.INFO)


class StationaryImu:
    """Stands in for the IMU in a bench demo: it always reports a cane that is not moving."""

    moving = False
    pitch_deg = None

    def window(self):
        return []


class PiNavigation:
    def __init__(self, options, distance, audio, *, amap=None, request_budget=None, report=None):
        if not isinstance(distance, PicoDistanceProvider):
            raise ValueError("Pi navigation requires the shared Pico distance input")
        cfg = load_config(options.get("config", "config/config.toml"))
        cfg.voice.enabled = cfg.voice.wake_enabled = False
        cfg.nav.read_route_with_tts = False
        # Turn prompts are pre-recorded clips. Use the Mandarin catalog only when it has been generated.
        language = options.get("language", "en")
        if language not in {"en", "cmn"}:
            raise ValueError("navigation.language must be en or cmn")
        if language != "en" and not (Path(cfg.audio.dir) / language).is_dir():
            LOG.warning("No %s navigation clips in %s; turn prompts stay in English", language, cfg.audio.dir)
            language = "en"
        cfg.general.language = language
        if amap is None and cfg.nav.enabled:
            # The request budget counts AMap requests. Google requests are not metered here.
            amap = map_client(cfg, fetch=measured_amap_fetch(request_budget, report) if report else None)
        self.phone_configured = False
        if options.get("phone_pair"):
            pair_dir = Path(options["phone_pair"])
            pair = PhonePair.load(pair_dir)
            cert, key = pair_dir / "server.crt", pair_dir / "server.key"
            if not any(pair.address in network for network in LAN_NETWORKS) or not cert.is_file() or not key.is_file():
                raise ValueError("Pi phone pairing requires a private LAN address and its generated certificate")
            if not cfg.web.enabled:
                raise ValueError("Pi phone pairing requires the navigation web service")
            cfg.web.phone_host = str(pair.address)
            cfg.web.phone_port = PHONE_PORT
            cfg.web.certfile, cfg.web.keyfile = str(cert), str(key)
            cfg.secrets.phone_token = pair.token
            self.phone_configured = True
        self.controller = SharedCane(cfg, distance, audio, amap)
        self.distance = distance
        self.distance.controller_owned = True
        audio.sink.catalog = Path(cfg.audio.dir) / language
        self.assume_stationary = bool(options.get("demo_assume_stationary", False))
        self.thread = threading.Thread(target=self.controller.run, name="pi-navigation", daemon=True)

    def start(self):
        from ..__main__ import _attach_imu

        app = self.controller
        _attach_imu(app)
        if app.imu is None and self.assume_stationary:
            # Without an IMU the heading can never be confirmed, so every turn prompt becomes
            # "stop and face ahead". For a bench demo with a simulated location only.
            LOG.warning("demo_assume_stationary: K1 confirms the heading without an IMU. Not for real walking.")
            app.imu = StationaryImu()
        self.thread.start()
        if self.phone_configured:
            from ..web.app import serve

            serve(app, app.cfg.web, app.cfg.secrets.phone_token, origin_control=app, paired_phone=True)

    def close(self):
        app = self.controller
        app.stop()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        if app.imu and hasattr(app.imu, "stop"):
            app.imu.stop()

    def status(self):
        app = self.controller
        with app._state_lock:
            return {"active": app.navigator.active, "route_pending": app._route_pending,
                    "location_valid": app._location_fresh(), "pico_ok": app._pico_ok,
                    "waiting_crossing": app.navigator.waiting_crossing is not None,
                    "route_service_configured": app.amap is not None}

    def attach_cloud_assistant(self, assistant):
        self.controller.cloud_assistant = assistant
        self.controller.cfg.voice.candidate_selection = True

    def range_report(self):
        """The cane's own view of the way ahead, for "can I walk" answers. None without fresh readings."""
        app = self.controller
        with app._state_lock:
            frame = app.frame if app._pico_ok else None
            if frame is None or app.clock() - frame.t > app.cfg.pico.timeout_s:
                return None
            hazards = app.hazards
            front = hazards.valid_distance(frame.front_m)
            if hazards.tf.role == "front" and frame.tfmini_strength >= hazards.tf.min_strength:
                laser = hazards.valid_distance(frame.tfmini_m)
                if laser is not None:
                    front = laser if front is None else min(front, laser)
            head = hazards.valid_distance(frame.up_m)
            near = max(hazards.h.stop_distance_m, hazards.warn_distance("obstacle_front"))
            return {"front_m": front, "head_m": head,
                    "front_blocked": front is not None and front <= near,
                    "head_blocked": head is not None and head <= hazards.h.head_warn_m}

    def progress(self):
        """Where the active route stands, for a spoken answer. None without an active route."""
        app = self.controller
        with app._state_lock:
            navigator = app.navigator
            if not navigator.active:
                return None
            position, remaining = navigator.position, navigator.remaining_m()
            if position is None or remaining is None:
                return {"located": False}
            step = navigator.route.steps[position.step]
            last = position.step >= len(navigator.route.steps) - 1
            return {"located": True, "remaining_m": remaining, "to_turn_m": position.to_step_end_m,
                    "maneuver": None if last else step.maneuver,
                    "crossing": navigator.waiting_crossing is not None,
                    # A left or right is only worth saying while the heading is confirmed.
                    "turns_ok": app.arbiter.orientation_ok}

    def find_places(self, query):
        """Search AMap near the iPhone and return places with their straight-line distance."""
        from ..voice.places import PlaceWorkerError
        from .destination_dialogue import Place, PlacesUnavailable, distance_m

        app = self.controller
        with app._state_lock:
            if not app.cfg.nav.enabled or app.amap is None or app.pi_places is None:
                raise PlacesUnavailable("地图服务不可用。")
            origin = app._search_origin_point()
        if origin is None:
            raise PlacesUnavailable("还没有收到手机定位。请在手机上开始共享定位。")
        try:
            results = app.pi_places.search(query, origin)
        except PlaceWorkerError:
            raise PlacesUnavailable("地图搜索失败。请检查网络后再试。") from None
        return [Place(place.id, place.name, place.address, place.lng, place.lat,
                      distance_m(origin, place.lng, place.lat))
                for place in results if place.coordinate_frame == "WGS84"]

    def stage_place(self, place):
        """Hold the chosen place until K1. The dialogue has already spoken the prompt."""
        from ..config import Destination
        from .destination_dialogue import PlacesUnavailable

        app = self.controller
        with app._state_lock:
            if not app._location_fresh() and app.manual_search_origin is None:
                raise PlacesUnavailable("手机定位已过期。请让手机保持共享定位。")
            app.destination_search.reset()
            app.pending_dest = Destination(place.id, place.name, lng=place.lng, lat=place.lat,
                                           address=place.address, coordinate_frame="WGS84")
            app.arbiter.discard_information()

    def clear_destination(self):
        app = self.controller
        with app._state_lock:
            app.pending_dest = None
            app.destination_search.reset()

    def cancel_destination(self):
        """Drop the chosen place, or stop the active route. Returns True when a route stopped."""
        app = self.controller
        with app._state_lock:
            active = app.navigator.active or app._route_pending
            if active:
                app.stop_navigation(announce=False)
            else:
                app.pending_dest = None
                app.destination_search.reset()
            return active

    def submit_destination(self, text):
        self.controller._jobs.put(lambda: self.controller.on_destination_text(text))

    def plan_route(self, destination):
        app = self.controller
        with app._state_lock:
            if not app.cfg.nav.enabled or app.amap is None:
                return NavigationReply(False, "The walking route service is unavailable.")
            if not app._location_fresh():
                return NavigationReply(False, "Share a fresh, precise iPhone location first.")
            if not app._pico_ok or not app.sm.nav_allowed or app.last_hazards or app.arbiter.safety_hold_active():
                return NavigationReply(False, "I can't start navigation right now.")
            preset = match_destination(destination, app.cfg.nav.destinations, cutoff=1.0)
            if preset is None:
                if not self.phone_configured:
                    return NavigationReply(False, "Pair the iPhone to choose this destination.")
                app.pending_dest = None
                app._search_destination(destination)
                return NavigationReply(True, "Choose the destination on your iPhone, then confirm it.")
            app.destination_search.reset()
            app.pending_dest = preset
            app._confirm()
            # The controller announces a route only after the provider and safety checks pass.
            return NavigationReply(app._route_pending, "")

    def get_next_instruction(self):
        app = self.controller
        with app._state_lock:
            if not app.navigator.active or not app._location_fresh():
                return NavigationReply(False, "There is no active route with a current location.")
            if app.navigator.waiting_crossing is not None:
                return NavigationReply(False, "Crossing ahead. Stop and assess it yourself.")
            if (app.last_nav is None or not app.navigator.is_current(app.last_nav)
                    or not app.sm.nav_allowed or not app._pico_ok or app.last_hazards
                    or app.arbiter.safety_hold_active() or app.clock() - app._last_nav_t > 15):
                return NavigationReply(False, "There is no current turn instruction to repeat.")
            app.repeat_last_nav()
            return NavigationReply(True, "")

    def get_navigation_status(self):
        state = self.status()
        if state["route_pending"]:
            return NavigationReply(True, "A walking route is being planned.")
        if state["active"] and state["location_valid"] and state["pico_ok"]:
            return NavigationReply(True, "Walking navigation is active.")
        return NavigationReply(False, "There is no active route with a current location.")

    def stop_navigation(self):
        app = self.controller
        with app._state_lock:
            active = app.navigator.active or app._route_pending or app.destination_search.id is not None
            app.stop_navigation(announce=False)
            return NavigationReply(active, "Navigation stopped." if active else "There is no active route.")
