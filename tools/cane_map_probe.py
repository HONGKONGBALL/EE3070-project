#!/usr/bin/env python3
"""Ask the configured map service for places and a walking route, and check the answer by geometry.

Run it on a computer or on the Pi before trusting a new key or a new service:

    ORS_KEY=... python tools/cane_map_probe.py --provider ors \
        --origin 114.1728,22.3364 --query 地铁站

It prints the places found, then every stretch of the route to the first place with the maneuver
the cane would announce at its end, next to the turn measured on the route's own geometry. A
maneuver that disagrees with the geometry is marked: that is how a service whose steps mean
something else than the cane assumes would show.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi"))

from cane.config import load_config  # noqa: E402
from cane.nav.coords import bearing_deg, haversine_m, wgs84_to_gcj02  # noqa: E402
from cane.nav.providers import PROVIDERS, map_client, map_frame  # noqa: E402

EXPECTED = {"turn_left": (-150, -55), "turn_right": (55, 150), "slight_left": (-55, -20), "slight_right": (20, 55),
            "go_straight": (-25, 25)}


def heading(points, *, at_end):
    ordered = points[::-1] if at_end else points
    for other in ordered[1:]:
        if haversine_m(*ordered[0], *other) >= 3.0:
            return bearing_deg(*other, *ordered[0]) if at_end else bearing_deg(*ordered[0], *other)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--provider", choices=PROVIDERS, default="ors")
    parser.add_argument("--origin", required=True, help="lng,lat in WGS-84, as a phone reports it")
    parser.add_argument("--query", default="地铁站")
    parser.add_argument("--to", help="lng,lat in WGS-84 to route to instead of the first place found")
    args = parser.parse_args()
    cfg = load_config(None, dict(os.environ))
    cfg.nav.provider = args.provider
    client = map_client(cfg)
    if client is None:
        parser.error("the key of this service is not set (AMAP_KEY, ORS_KEY or GOOGLE_MAPS_KEY)")
    shifted = map_frame(client) == "gcj02"
    origin = tuple(float(part) for part in args.origin.split(","))
    here = wgs84_to_gcj02(*origin) if shifted else origin
    print(f"service: {args.provider}, frame: {map_frame(client)}")

    target = None
    if args.to:
        to = tuple(float(part) for part in args.to.split(","))
        target = wgs84_to_gcj02(*to) if shifted else to
    else:
        places = client.search_places(args.query, here)
        print(f"places for {args.query!r}: {len(places)}")
        for place in places[:6]:
            print(f"  {haversine_m(*here, place.lng, place.lat):6.0f} m  {place.name}  | {place.address}")
        if not places:
            return 1
        target = (places[0].lng, places[0].lat)

    route = client.walking_route(here, target)
    line = route.polyline
    print(f"route: {route.distance_m:.0f} m, {route.duration_s / 60:.1f} min, {len(route.steps)} stretches, "
          f"starts {haversine_m(*here, *line[0]):.0f} m from the origin, "
          f"ends {haversine_m(*target, *line[-1]):.0f} m from the target")
    odd = 0
    for index, step in enumerate(route.steps):
        measured = ""
        if index + 1 < len(route.steps):
            arriving = heading(step.polyline, at_end=True)
            leaving = heading(route.steps[index + 1].polyline, at_end=False)
            if arriving is not None and leaving is not None:
                turn = (leaving - arriving + 180) % 360 - 180
                low, high = EXPECTED.get(step.maneuver or "", (-181, 181))
                agrees = low - 20 <= turn <= high + 20
                odd += not agrees
                measured = f"geometry turns {turn:+4.0f}°" + ("" if agrees else "   <-- DISAGREES")
        crossing = "  [crossing]" if step.is_crossing else ""
        print(f"  [{index}] {step.distance_m:4.0f} m, then {step.maneuver or 'arrive' if index + 1 == len(route.steps) else step.maneuver or '(nothing)'}"
              f" ({step.action or '-'})  {measured}{crossing}\n        text: {step.instruction}")
    print("every maneuver agrees with the geometry" if not odd else f"{odd} maneuver(s) disagree with the geometry")
    return 0 if not odd else 2


if __name__ == "__main__":
    raise SystemExit(main())
