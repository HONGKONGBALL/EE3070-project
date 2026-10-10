"""Main program: wires sensors -> hazards/state machine/navigation -> arbiter.

Threads:
- main loop (20 Hz): reads the Pico inbox, IMU state, runs hazards, nav, arbiter
- Pico reader, IMU sampler, heading tracker, stereo scan, voice, AMap requests:
  background threads that hand results back through `self._jobs`
- web server (FastAPI/uvicorn): read-only status + phone location + text input

Safety assessment never waits on the network, the web server or the cameras.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .arbiter import OutputArbiter
from .audio import AplayAudio, ConsoleAudio, load_durations
from .config import LANGUAGES, Config, Destination
from .hazard import HazardAssessor
from .logbook import Logbook
from .model import ButtonEvent, Direction, OutputRequest, Priority, SensorFrame
from .nav.amap import AmapClient, AmapError, NetworkError
from .nav.coords import wgs84_to_gcj02
from .nav.navigator import Navigator, NavOutput
from .pico_link import PicoLinkBase, PicoWarning, PowerEvent, SerialPicoLink, SimPicoLink
from .prompts import CATALOG, destination_prompt_id, longest_duration
from .state_machine import CaneStateMachine, State
from .terrain import RoughnessClassifier, vibration_features
from .voice.destinations import match_destination

log = logging.getLogger(__name__)

LOCATION_STALE_S = 15.0
VSYS_LOW_V = 4.5


class CaneApp:
    def __init__(
        self,
        cfg: Config,
        sim: bool = False,
        pico: PicoLinkBase | None = None,
        audio=None,
        amap: AmapClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        imu=None,
        start_threads: bool = True,
    ) -> None:
        self.cfg = cfg
        self.sim = sim
        self.clock = clock
        self.lang = cfg.general.language
        self.test_mode = cfg.general.test_mode
        self.logbook = Logbook(cfg.general.log_dir)

        durations = load_durations(cfg.audio.dir)
        clip_s = {}
        for pid in CATALOG:
            try:
                clip_s[pid] = longest_duration(durations, pid)
            except KeyError:
                pass
        if audio is None:
            audio = (ConsoleAudio(self.lang, durations.get(self.lang)) if sim
                     else AplayAudio(cfg.audio.dir, self.lang, cfg.audio.device, cfg.audio.player))
        self.audio = audio
        self.pico = pico or (SimPicoLink() if sim else SerialPicoLink(cfg.pico.port, cfg.pico.baud))
        self.arbiter = OutputArbiter(audio, self.pico.vibrate, cfg.arbiter.safety_hold_s,
                                     cfg.arbiter.repeat_cooldown_s, clock)
        self.sm = CaneStateMachine(clock)
        self.sm.test_mode = self.test_mode
        self.sm.on_transition(lambda tr: self.logbook.write("state", src=tr.src.value, dst=tr.dst.value, event=tr.event))
        self.hazards = HazardAssessor(cfg.hazard, cfg.tfmini, clip_s)
        self.navigator = Navigator(cfg.nav)
        self.roughness = RoughnessClassifier()

        if amap is None and cfg.nav.enabled and cfg.secrets.amap_key:
            amap = AmapClient(cfg.secrets.amap_key)
        self.amap = amap

        self.imu = imu        # imu.ImuRunner (or anything with .moving, .pitch_deg, .window())
        self.vision = None    # vision.runner.VisionRunner, attached by __main__ if cameras work
        self._coord_offset: tuple[float, float] | None = None

        self._jobs: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._start_threads = start_threads
        self.frame: SensorFrame | None = None
        self.tf_window: deque[float] = deque(maxlen=40)
        self.last_hazards: list[str] = []
        self.terrain_class = "unknown"
        self.location: tuple[float, float, float, float] | None = None  # lng, lat, acc, t
        self.pending_dest: Destination | None = None
        self.last_nav: NavOutput | None = None
        self.vsys: float | None = None
        self.listening = False
        self._last_hb = 0.0
        self._pico_ok = False
        self._last_scan_entered = -1.0
        self._stop = threading.Event()

    # ================================================================ output
    def say(self, prompt_id: str, priority: Priority | None = None, *, extra: list[str] | None = None,
            direction: Direction = Direction.NONE, text: str | None = None) -> bool:
        p = CATALOG.get(prompt_id)
        prio = priority if priority is not None else (p.priority if p else Priority.INFO)
        vib = p.vibration if p else None
        self.arbiter.set_orientation_ok(self.sm.turns_allowed)
        ok = self.arbiter.submit(OutputRequest(prompt_id, prio, direction, vib, text, extra or []))
        return ok

    # ================================================================== loop
    def start(self) -> None:
        self.pico.start()
        self.sm.handle("boot_done")
        self.say("boot_ready")
        if self.test_mode:
            self.say("test_mode_on")
        self.logbook.write("boot", sim=self.sim, lang=self.lang, test_mode=self.test_mode,
                           secrets=repr(self.cfg.secrets))

    def run(self, hz: float = 20.0) -> None:
        self.start()
        period = 1.0 / hz
        while not self._stop.is_set():
            t0 = self.clock()
            self.step()
            time.sleep(max(0.0, period - (self.clock() - t0)))

    def stop(self) -> None:
        self._stop.set()
        self.pico.close()

    def step(self) -> None:
        now = self.clock()
        if now - self._last_hb >= self.cfg.pico.heartbeat_s:
            self.pico.heartbeat()
            self._last_hb = now

        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            job()

        while True:
            try:
                msg = self.pico.inbox.get_nowait()
            except queue.Empty:
                break
            self._on_pico(msg)

        self._check_pico(now)
        self._update_motion()
        self._run_hazards()
        self._check_location(now)
        self.arbiter.set_orientation_ok(self.sm.turns_allowed)
        self.arbiter.tick()

    # ------------------------------------------------------------- pico
    def _on_pico(self, msg) -> None:
        if isinstance(msg, SensorFrame):
            self.frame = msg
            if msg.tfmini_m is not None:
                self.tf_window.append(msg.tfmini_m)
        elif isinstance(msg, ButtonEvent):
            self.logbook.write("button", button=msg.button, ev=msg.kind)
            self.on_button(msg.button, msg.kind)
        elif isinstance(msg, PowerEvent):
            self.vsys = msg.vsys
            if msg.vsys < VSYS_LOW_V:
                self.say("battery_low")
        elif isinstance(msg, PicoWarning):
            self.logbook.write("pico_warning", msg=msg.msg)

    def _check_pico(self, now: float) -> None:
        fresh = self.frame is not None and (now - self.frame.t) <= self.cfg.pico.timeout_s
        if fresh and not self._pico_ok:
            self._pico_ok = True
            if self.sm.state is State.FAULT:
                self.sm.handle("recovered")
                self.say("sensor_ok")
        elif not fresh and self._pico_ok:
            self._pico_ok = False
            self.sm.handle("fault")
            self.say("pico_lost")
            self.logbook.write("fault", reason="pico timeout")

    # ----------------------------------------------------------- motion
    def _update_motion(self) -> None:
        if self.imu is None:
            return
        moving = bool(self.imu.moving)
        if moving and self.sm.state in (State.READY, State.STOPPED):
            self.sm.handle("motion_start")
        elif not moving and self.sm.state is State.WALKING:
            self.sm.handle("motion_stop")
        if self.sm.state is State.STOPPED and self.sm.entered_at != self._last_scan_entered \
                and self.sm.time_in_state() >= 1.0:
            self._last_scan_entered = self.sm.entered_at
            self._start_stereo_scan()
        if self.vision is not None:
            self.vision.walking = self.sm.state is State.WALKING
            h = self.vision.heading
            if h is not None and self.sm.state is State.WALKING and not h.orientation_ok:
                self.sm.handle("orientation_lost")

    def roughness_tick(self) -> None:
        """Called by the IMU thread once per window."""
        if self.imu is None:
            return
        win = self.imu.window()
        if len(win) < 16:
            return
        f = vibration_features(win, self.cfg.imu.rate_hz, list(self.tf_window))
        cls = self.roughness.classify(f, self.sm.state is State.WALKING)
        self._jobs.put(lambda: self._on_terrain(cls, f))

    def _on_terrain(self, cls: str, f) -> None:
        if cls != self.terrain_class:
            self.logbook.write("terrain", cls=cls, rms=round(f.rms, 3), tf_std=round(f.tf_std, 3))
        self.terrain_class = cls
        if cls == "rough":
            self.say("rough_ground")
        elif cls == "soft":
            self.say("soft_ground")

    # ---------------------------------------------------------- hazards
    def _run_hazards(self) -> None:
        if self.frame is None or self.sm.state is State.FAULT:
            return
        moving = self.sm.state is State.WALKING if self.imu is not None else True
        tilt = self.imu.pitch_deg if self.imu is not None and self.imu.pitch_deg is not None else None
        found = self.hazards.assess(self.frame, moving=moving, tilt_deg=tilt)
        self.last_hazards = [h.kind for h in found]
        for h in found:
            if self.say(h.prompt_id):
                self.logbook.write("hazard", hazard=h.kind, d=h.distance_m)

    # ----------------------------------------------------------- stereo
    def _start_stereo_scan(self) -> None:
        if self.vision is None:
            return
        pitch = (self.imu.pitch_deg if self.imu is not None else None) or 0.0
        self.vision.request_scan(pitch, lambda res: self._jobs.put(lambda: self._on_scan(res)))

    def _on_scan(self, res) -> None:
        if not self.sm.state.stationary:
            return  # user walked off before the scan finished; result is stale
        self.logbook.write("scan", prompts=res.prompts, uphill=round(res.uphill_deg, 1),
                           left_down=round(res.left_down_deg, 1), step_d=res.step_distance_m)
        for pid in res.prompts:
            self.say(pid)

    # ---------------------------------------------------------- buttons
    def on_button(self, button: str, kind: str) -> None:
        if button == "ok" and kind == "short":
            if self.sm.state is State.CROSSING:
                self._handle_nav_outputs(self.navigator.confirm_crossing())
                self.sm.handle("crossing_confirmed")
            elif self.pending_dest is not None:
                d, self.pending_dest = self.pending_dest, None
                self.start_navigation(d)
            else:
                if self.sm.state.stationary:
                    self.sm.handle("aligned")
                    if self.vision is not None and self.vision.heading is not None:
                        self.vision.heading.reset_alignment()
                self.repeat_last_nav()
        elif button == "dest" and kind == "short":
            self.start_voice_input()
        elif button == "dest" and kind == "long":
            self.stop_navigation()
        elif button == "repeat" and kind == "short":
            self.repeat_last_nav()
        elif button == "repeat" and kind == "long":
            self.cycle_language()
        elif button == "mode" and kind == "short":
            self.pico.light(0.0 if getattr(self, "_light_on", False) else 0.8)
            self._light_on = not getattr(self, "_light_on", False)
        elif button == "mode" and kind == "long":
            self.test_mode = not self.test_mode
            self.sm.test_mode = self.test_mode
            if self.test_mode:
                self.say("test_mode_on")
            self.logbook.write("test_mode", on=self.test_mode)

    def cycle_language(self) -> None:
        i = LANGUAGES.index(self.lang)
        self.lang = LANGUAGES[(i + 1) % len(LANGUAGES)]
        if hasattr(self.audio, "lang"):
            self.audio.lang = self.lang
        self.say("lang_changed")

    def repeat_last_nav(self) -> None:
        if self.last_nav is not None and self.navigator.active:
            o = self.last_nav
            self.arbiter.allow_repeat(OutputRequest(o.prompt_id, Priority.NAV, extra_clips=o.extra_clips, text=o.text))
            self.say(o.prompt_id, extra=o.extra_clips, direction=o.direction, text=o.text)

    # ------------------------------------------------------------ voice
    def start_voice_input(self) -> None:
        if not self.cfg.voice.enabled or self.listening:
            return
        self.say("voice_listening")
        self.listening = True

        def work():
            try:
                text = self._record_and_transcribe()
            except Exception as e:
                log.warning("voice input failed: %s", e)
                text = ""
            self._jobs.put(lambda: self.on_destination_text(text))

        self._spawn(work, "voice")

    def _record_and_transcribe(self) -> str:
        from .voice.asr import VoskRecognizer
        from .voice.recorder import record_wav

        # wait for the "say destination" prompt to finish so we don't record ourselves
        deadline = time.monotonic() + 3.0
        while self.audio.busy() and time.monotonic() < deadline:
            time.sleep(0.05)
        wav = record_wav(Path(self.cfg.general.log_dir) / "last_voice.wav", self.cfg.voice.record_s,
                         self.cfg.voice.device)
        if self.cfg.voice.backend == "vosk":
            phrases = [a for d in self.cfg.nav.destinations for a in [d.name, *d.aliases]]
            return VoskRecognizer(self.cfg.voice.vosk_model_dir, self.lang, phrases).transcribe(wav)
        return ""  # keyword backend needs text from the diagnostics page

    def on_destination_text(self, text: str) -> dict:
        """Entry point for recognised speech (also used by the diagnostics page)."""
        self.listening = False
        d = match_destination(text, self.cfg.nav.destinations)
        self.logbook.write("voice", text=text, match=d.id if d else None)
        if d is None:
            self.say("voice_not_understood")
            return {"matched": None}
        self.pending_dest = d
        # read back the pre-generated destination name, then ask for OK
        self.say(destination_prompt_id(d.id), Priority.INFO, extra=["dest_confirm"])
        return {"matched": d.id, "name": d.name}

    # -------------------------------------------------------------- nav
    def on_location(self, lng: float, lat: float, accuracy: float) -> None:
        """Called from the web thread; processed in the main loop."""
        t = self.clock()
        self._jobs.put(lambda: self._on_location(lng, lat, accuracy, t))

    def _to_amap(self, lng: float, lat: float) -> tuple[float, float]:
        """WGS-84 -> AMap coordinates. May call the network (amap_api mode)."""
        mode = self.cfg.nav.coord_mode
        if mode == "offline_gcj02":
            return wgs84_to_gcj02(lng, lat)
        if mode == "amap_api" and self.amap is not None:
            c = self.amap.convert_gps(lng, lat)
            # the offset is ~constant over a few km; reuse it for per-fix updates
            self._coord_offset = (c[0] - lng, c[1] - lat)
            return c
        return lng, lat

    def _to_amap_fast(self, lng: float, lat: float) -> tuple[float, float]:
        """Per-fix conversion without network calls."""
        mode = self.cfg.nav.coord_mode
        if mode == "offline_gcj02":
            return wgs84_to_gcj02(lng, lat)
        if mode == "amap_api" and self._coord_offset is not None:
            return lng + self._coord_offset[0], lat + self._coord_offset[1]
        return lng, lat

    def _on_location(self, lng: float, lat: float, acc: float, t: float) -> None:
        self.location = (lng, lat, acc, t)
        if not self.navigator.active or not self.sm.nav_allowed:
            return
        p = self._to_amap_fast(lng, lat)
        self._handle_nav_outputs(self.navigator.update(p[0], p[1], acc))

    def _check_location(self, now: float) -> None:
        if self.navigator.active and (self.location is None or now - self.location[3] > LOCATION_STALE_S):
            # phone (= hotspot) is gone: navigation exits, obstacle avoidance carries on
            self.navigator.stop()
            self.say("net_lost_nav_off")
            self.logbook.write("nav", event="stopped", reason="location stale")

    def _handle_nav_outputs(self, outs: list[NavOutput]) -> None:
        for o in outs:
            self.logbook.write("nav", event=o.kind, prompt=o.prompt_id, extra=o.extra_clips)
            if o.kind == "crossing":
                self.sm.handle("crossing_detected")
            elif o.kind == "reroute" and self.navigator.dest is not None:
                self._request_route(self.navigator.dest, reroute=True)
            if o.kind == "prompt" and o.prompt_id not in ("nav_start", "crossing_confirmed"):
                self.last_nav = o
            self.say(o.prompt_id, extra=o.extra_clips, direction=o.direction, text=o.text)

    def start_navigation(self, d: Destination) -> None:
        if self.amap is None:
            self.say("route_failed")
            self.logbook.write("nav", event="failed", reason="no AMAP_KEY")
            return
        if self.location is None:
            self.say("no_network")
            return
        def work():
            try:
                dd = self._to_amap(d.lng, d.lat)
            except (NetworkError, AmapError) as e:
                log.warning("dest conversion failed: %s", e)
                self._jobs.put(lambda: self.say("route_failed"))
                return
            self._request_route_blocking(dd, reroute=False)

        self._spawn(work, "route")

    def _request_route(self, dest: tuple[float, float], reroute: bool) -> None:
        self._spawn(lambda: self._request_route_blocking(dest, reroute), "route")

    def _request_route_blocking(self, dest: tuple[float, float], reroute: bool) -> None:
        assert self.amap is not None and self.location is not None
        lng, lat = self.location[0], self.location[1]
        try:
            origin = self._to_amap(lng, lat)
            route = self.amap.walking_route(origin, dest)
        except NetworkError:
            self._jobs.put(lambda: (self.navigator.stop(), self.say("net_lost_nav_off")))
            return
        except AmapError as e:
            log.warning("route failed: %s", e)
            self._jobs.put(lambda: self.say("route_failed"))
            return

        def apply():
            outs = self.navigator.start(route, dest)
            if reroute:
                outs = []  # "off route, rerouting" was already said
            self.logbook.write("nav", event="route", steps=len(route.steps), distance=route.distance_m)
            self._handle_nav_outputs(outs)

        self._jobs.put(apply)

    def stop_navigation(self) -> None:
        if self.navigator.active:
            self.navigator.stop()
            if self.sm.state is State.CROSSING:
                self.sm.handle("nav_cancel")
            self.say("nav_stopped")

    # ---------------------------------------------------------- helpers
    def _spawn(self, fn: Callable[[], None], name: str) -> None:
        if self._start_threads:
            threading.Thread(target=fn, daemon=True, name=name).start()
        else:
            fn()

    # ----------------------------------------------------------- status
    def status(self) -> dict:
        """Snapshot for the diagnostics page. Never includes secrets."""
        f = self.frame
        pos = self.navigator.position
        return {
            "time": time.time(),
            "state": self.sm.state.value,
            "aligned": self.sm.aligned,
            "test_mode": self.test_mode,
            "sim": self.sim,
            "language": self.lang,
            "pico_ok": self._pico_ok,
            "vsys": self.vsys,
            "sensors": None if f is None else {
                "front_m": f.front_m, "up_m": f.up_m, "tfmini_m": f.tfmini_m,
                "tfmini_strength": f.tfmini_strength, "age_s": round(self.clock() - f.t, 2),
            },
            "imu": None if self.imu is None else {"moving": self.imu.moving, "pitch_deg": self.imu.pitch_deg},
            "terrain": self.terrain_class,
            "hazards": self.last_hazards,
            "safety_hold": self.arbiter.safety_hold_active(),
            "orientation_ok": self.arbiter.orientation_ok,
            "speaking": self.arbiter.current.prompt_id if self.arbiter.current else None,
            "queue": self.arbiter.pending(),
            "recent_outputs": [asdict(e) for e in list(self.arbiter.history)[-15:]],
            "nav": {
                "active": self.navigator.active,
                "amap_key_configured": self.amap is not None,
                "waiting_crossing": self.navigator.waiting_crossing is not None,
                "step": None if pos is None else pos.step,
                "to_step_end_m": None if pos is None else round(pos.to_step_end_m, 1),
                "location_age_s": None if self.location is None else round(self.clock() - self.location[3], 1),
                "pending_destination": self.pending_dest.name if self.pending_dest else None,
            },
        }

    def log_files(self) -> list[Path]:
        return self.logbook.files()
