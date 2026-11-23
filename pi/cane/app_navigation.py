"""Route I/O generations and current-location navigation gates."""

from __future__ import annotations

import logging
import math

from .app_context import AppContext
from .config import Destination
from .model import Priority
from .nav.amap import AmapError, NetworkError, WalkingRouteBlocked, WalkingRouteUnavailable
from .nav.coords import wgs84_to_gcj02
from .nav.navigator import NavOutput
from .power import PowerStage
from .state_machine import State

log = logging.getLogger(__name__)
LOCATION_STALE_S = 15.0


class NavigationMixin:
    def on_location(self: AppContext, lng: float, lat: float, accuracy: float) -> None:
        """Location ingress is queued; invalid GPS fixes never refresh a route."""
        app = self
        if not all(math.isfinite(value) for value in (lng, lat, accuracy)):
            return
        if not (-180 <= lng <= 180 and -90 <= lat <= 90 and accuracy >= 0):
            return
        timestamp = app.clock()
        app._jobs.put(lambda: app._on_location(lng, lat, accuracy, timestamp))

    def _location_fresh(self: AppContext) -> bool:
        app = self
        return (app.location is not None
                and 0 <= app.clock() - app.location[3] <= LOCATION_STALE_S
                and app.location[2] <= app.navigator.max_accuracy_m)

    def _to_amap(self: AppContext, lng: float, lat: float) -> tuple[float, float]:
        app = self
        mode = app.cfg.nav.coord_mode
        if mode == "offline_gcj02":
            return wgs84_to_gcj02(lng, lat)
        if mode == "amap_api" and app.amap is not None:
            converted = app.amap.convert_gps(lng, lat)
            app._coord_offset = (converted[0] - lng, converted[1] - lat)
            return converted
        return lng, lat

    def _to_amap_fast(self: AppContext, lng: float, lat: float) -> tuple[float, float]:
        app = self
        if app.cfg.nav.coord_mode == "offline_gcj02":
            return wgs84_to_gcj02(lng, lat)
        if app.cfg.nav.coord_mode == "amap_api" and app._coord_offset is not None:
            return lng + app._coord_offset[0], lat + app._coord_offset[1]
        return lng, lat

    def _on_location(self: AppContext, lng: float, lat: float, acc: float, timestamp: float) -> None:
        app = self
        if app.location is not None and timestamp < app.location[3]:
            return
        was_fresh = app._location_fresh()
        app.location = (lng, lat, acc, timestamp)
        search = app.destination_search
        if (app.nearby_search and not app.listening
                and app.manual_search_origin is None
                and not was_fresh and app._location_fresh()
                and search.state in {"waiting_origin", "searching", "results"} and not search.distances):
            app._search_destination(search.query)
        if not app.navigator.active or not app.sm.nav_allowed or not app._location_fresh():
            if not app._location_fresh():
                app.last_nav = None
                app.arbiter.discard_navigation()
            return
        previous_step = app.navigator.position.step if app.navigator.position else None
        position = app._to_amap_fast(lng, lat)
        outputs = app.navigator.update(position[0], position[1], acc)
        new_step = app.navigator.position.step if app.navigator.position else None
        # Directions for a step are obsolete once the user is on another one. The first fix on a
        # new route has no step before it: discarding then cut "navigation started" and "off
        # route, rerouting" short, and removed what the new route had to say first.
        if previous_step is not None and previous_step != new_step:
            app.last_nav = None
            app.arbiter.discard_navigation()
        app._handle_nav_outputs(outputs)

    def _check_location(self: AppContext, now: float) -> None:
        app = self
        search = app.destination_search
        if (app.pi_places is not None and app.manual_search_origin is None
                and search.state == "results" and not app._location_fresh()):
            search.state = "waiting_origin"
            search.choices = ()
            search.distances = {}
        if (app.navigator.active or app._route_pending) and (
            app.location is None or now - app.location[3] > LOCATION_STALE_S
        ):
            app.stop_navigation(announce=False)
            app.say("net_lost_nav_off", Priority.SYSTEM)
            app.logbook.write("nav", event="stopped", reason="location stale")

    def _handle_nav_outputs(self: AppContext, outputs: list[NavOutput]) -> None:
        app = self
        for output in outputs:
            app.logbook.write("nav", event=output.kind, prompt=output.prompt_id,
                               extra=output.extra_clips)
            if output.kind in ("crossing", "reroute", "arrived"):
                app.last_nav = None
                app.arbiter.discard_navigation()
            if output.kind == "crossing":
                app.sm.handle("crossing_detected")
            elif output.kind == "reroute" and app.navigator.dest is not None:
                app._request_route(app.navigator.dest, reroute=True)
            if output.kind == "prompt" and output.prompt_id not in ("nav_start", "crossing_confirmed"):
                app.last_nav, app._last_nav_t = output, app.clock()
            if app.sm.paused or app.sm.state is State.FAULT:
                continue
            app.say(output.prompt_id, extra=output.extra_clips,
                     direction=output.direction, text=output.text)

    def start_navigation(self: AppContext, destination: Destination) -> None:
        app = self
        if destination.coordinate_frame == "GCJ02" and (
            not app.cfg.nav.provider_coordinates_verified or app.cfg.nav.coord_mode == "none"
        ):
            app.say("route_failed", Priority.SYSTEM)
            app.logbook.write("nav", event="failed", reason="provider_coordinates_require_verification")
            return
        if not app.cfg.nav.enabled or app.amap is None:
            app.say("route_failed")
            app.logbook.write("nav", event="failed", reason="route_service_unavailable")
            return
        if (not app._location_fresh() or not app.sm.nav_allowed or not app._pico_ok
                or app.power.stage is PowerStage.CRITICAL):
            app.say("resume_blocked", Priority.SYSTEM)
            return
        app._route_generation += 1
        generation = app._route_generation
        app._route_pending = True
        app.navigator.invalidate()
        app.last_nav = None
        app.arbiter.discard_navigation()

        def work() -> None:
            try:
                target = ((destination.lng, destination.lat) if destination.coordinate_frame == "GCJ02"
                          else app._to_amap(destination.lng, destination.lat))
            except (NetworkError, AmapError) as error:
                log.warning("destination conversion failed: %s", error)
                app._jobs.put(lambda: app._route_error(generation, "route_failed"))
                return
            app._request_route_blocking(target, False, generation)

        app._spawn(work, "route")

    def _request_route(self: AppContext, destination: tuple[float, float], reroute: bool) -> None:
        app = self
        app._route_generation += 1
        generation = app._route_generation
        app._route_pending = True
        app._spawn(lambda: app._request_route_blocking(destination, reroute, generation), "route")

    def _route_error(self: AppContext, generation: int, prompt_id: str) -> None:
        app = self
        if generation != app._route_generation:
            return
        app._route_pending = False
        app.navigator.stop()
        app.last_nav = None
        app.arbiter.discard_navigation()
        app.logbook.write("nav", event="failed", reason=prompt_id)
        app.say(prompt_id, Priority.SYSTEM)

    def _request_route_blocking(self: AppContext, destination: tuple[float, float], reroute: bool,
                                generation: int | None = None) -> None:
        app = self
        generation = app._route_generation if generation is None else generation
        location, client = app.location, app.amap
        if location is None or client is None:
            app._jobs.put(lambda: app._route_error(generation, "route_failed"))
            return
        try:
            origin = app._to_amap(location[0], location[1])
            route = client.walking_route(origin, destination)
        except NetworkError:
            app._jobs.put(lambda: app._route_error(generation, "net_lost_nav_off"))
            return
        except WalkingRouteUnavailable:
            app._jobs.put(lambda: app._route_error(generation, "walk_route_unavailable"))
            return
        except WalkingRouteBlocked:
            app._jobs.put(lambda: app._route_error(generation, "walk_route_blocked"))
            return
        except AmapError as error:
            log.warning("route failed: %s", error)
            app._jobs.put(lambda: app._route_error(generation, "route_failed"))
            return
        if route.blocks_continuous_walking:
            app._jobs.put(lambda: app._route_error(generation, "walk_route_blocked"))
            return

        def apply() -> None:
            if (generation != app._route_generation or not app._location_fresh()
                    or not app.sm.nav_allowed or not app._pico_ok):
                app.logbook.write("nav", event="route_discarded", reason="cancelled_or_unsafe")
                return
            app._route_pending = False
            outputs = app.navigator.start(route, destination)
            app.logbook.write("nav", event="route", steps=len(route.steps), distance=route.distance_m)
            if route.uses_alternative:
                app.say("walk_route_alternative", Priority.SYSTEM)
            # A user who left the route needs to hear at once which way the new one goes. At the
            # start the first instruction follows "navigation started": without a confirmed
            # heading the arbiter turns it into "stop and align", and the K1 press that confirms
            # the heading then repeats it instead of answering "no current instruction".
            app._handle_nav_outputs(app.navigator.rerouted() if reroute
                                    else outputs + app.navigator.first_instruction())

        app._jobs.put(apply)

    def _cancel_pending_route_for_pause(self: AppContext) -> None:
        """End an interrupted replacement request instead of retaining a suspended route."""
        app = self
        if not app._route_pending or not app.navigator.active:
            return
        app.stop_navigation(announce=False)
        app.say("nav_stopped", Priority.SYSTEM)
        app.logbook.write("nav", event="stopped", reason="paused_during_route_request",
                          restart="select_destination")

    def stop_navigation(self: AppContext, announce: bool = True) -> None:
        app = self
        active = app.navigator.active or app._route_pending or app.pending_dest is not None
        app._route_generation += 1
        app._route_pending = False
        app.pending_dest = None
        app.destination_search.reset()
        app.cancel_voice_input()
        app.last_nav = None
        app.navigator.stop()
        app.sm.handle("nav_cancel")
        app.arbiter.discard_navigation()
        app.arbiter.discard_information()
        if active and announce:
            app.say("nav_stopped", Priority.SYSTEM)
        app.logbook.write("nav", event="stopped", user_requested=announce)
