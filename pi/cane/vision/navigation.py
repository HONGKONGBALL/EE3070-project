"""Connect the Pi 5 navigation controller to the shared camera service."""

import logging
from pathlib import Path
import threading
import time

from ..app import CaneApp
from ..config import load_config
from ..model import Priority, SensorFrame
from ..nav.amap import AmapClient
from ..prompts import destination_prompt_id
from ..voice.destinations import match_destination
from ..voice.pi_places import PiPlaceSearch
from ..web.pairing import LAN_NETWORKS, PHONE_PORT, PhonePair
from .distance import PicoDistanceProvider
from .voice_commands import NavigationReply


LOG = logging.getLogger(__name__)


class SharedCane(CaneApp):
    def __init__(self, cfg, distance, audio, amap):
        self.distance = distance
        super().__init__(cfg, pico=distance.link, audio=audio, amap=amap,
                         pi_places=PiPlaceSearch(amap), output_arbiter=audio.arbiter)

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
        # The assistant owns recording. The legacy short K2 gesture had its own recorder.
        if (button, kind) != ("dest", "short"):
            super().on_button(button, kind)

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


class PiNavigation:
    def __init__(self, options, distance, audio, *, amap=None):
        if not isinstance(distance, PicoDistanceProvider):
            raise ValueError("Pi navigation requires the shared Pico distance input")
        cfg = load_config(options.get("config", "config/config.toml"))
        cfg.voice.enabled = cfg.voice.wake_enabled = False
        cfg.nav.read_route_with_tts = False
        cfg.general.language = "en"
        if amap is None and cfg.nav.enabled and cfg.secrets.amap_key:
            amap = AmapClient(cfg.secrets.amap_key)
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
        audio.sink.catalog = Path(cfg.audio.dir) / "en"
        self.thread = threading.Thread(target=self.controller.run, name="pi-navigation", daemon=True)

    def start(self):
        from ..__main__ import _attach_imu

        app = self.controller
        _attach_imu(app)
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
