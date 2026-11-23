#!/usr/bin/env python3
"""Stand in for the iPhone with a simulated location in Shenzhen, for the class demo.

Why: AMap counts every search or route that touches Hong Kong as overseas data, and this
project's key has no overseas quota (error 10045). Mainland coordinates work. This script
reports a fixed point in Shenzhen to the cane's navigation service, so that
K2 -> candidates -> "the second one" -> K1 -> route runs end to end. Once K1 starts a route it
walks the reported position along that route at walking speed, so the turn prompts and the
arrival prompt play as well.

Run it ON THE PI while the camera service is up. Stop location sharing in the iPhone app first,
otherwise real Hong Kong fixes and simulated ones alternate.

    set -a; . ~/.config/smart-cane/psyche.env; set +a
    python3 cane_demo_location.py                      # fixed point, then follow the route after K1
    python3 cane_demo_location.py --no-walk            # only hold the fixed point
    python3 cane_demo_location.py --origin-gcj 113.9305,22.5333

On a computer without the cane it can rehearse the map side only:

    python3 cane_demo_location.py --dry-run --dest 市民中心

The location is simulated. Say so when presenting.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

AMAP = "https://restapi.amap.com"
DEFAULT_RELEASE = "~/.smart-cane-deploy/psyche-k2-85a4a0f/release"
# A street corner in Buji, Shenzhen (GCJ-02, the frame AMap shows): 377 m on foot from Dexing metro
# station, with several turns on the way. The cane sends AMap a 0.1-degree grid anchor instead of the
# exact position, and that anchor must stay on the mainland too: from most of Futian and Nanshan it
# rounds to 22.5 N, which lies on the Hong Kong side and is rejected. This point rounds to 114.1, 22.6.
DEFAULT_ORIGIN_GCJ = (114.11102, 22.59047)
A, EE = 6378245.0, 0.00669342162296594323
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def say(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


# ---- coordinates -------------------------------------------------------------------------
def _tlat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _tlng(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng, lat):
    dlat, dlng = _tlat(lng - 105.0, lat - 35.0), _tlng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = 1 - EE * math.sin(radlat) ** 2
    root = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * root) * math.pi)
    dlng = (dlng * 180.0) / (A / root * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng, lat):
    """The phone reports WGS-84, so route points from AMap are converted back."""
    raw_lng, raw_lat = lng, lat
    for _ in range(4):
        converted_lng, converted_lat = wgs84_to_gcj02(raw_lng, raw_lat)
        raw_lng += lng - converted_lng
        raw_lat += lat - converted_lat
    return raw_lng, raw_lat


def metres(a, b):
    lng0, lat0, lng1, lat1 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat1 - lat0) / 2) ** 2 + math.cos(lat0) * math.cos(lat1) * math.sin((lng1 - lng0) / 2) ** 2
    return 6_371_000 * 2 * math.asin(min(1.0, math.sqrt(h)))


# ---- AMap --------------------------------------------------------------------------------
def amap(path, **params):
    key = os.environ.get("AMAP_KEY", "").strip()
    if not key:
        raise RuntimeError("AMAP_KEY is not set in the environment")
    url = AMAP + path + "?" + urllib.parse.urlencode(dict(params, key=key, output="JSON"))
    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.load(response)
    if str(data.get("status")) != "1":
        raise RuntimeError(f"AMap rejected {path}: {data.get('info')} ({data.get('infocode')})")
    return data


def cane_candidates(query, origin_wgs, count=3):
    """What the cane would read out: AMap is asked around the grid anchor, the Pi ranks by the exact origin."""
    anchor = wgs84_to_gcj02(round(origin_wgs[0], 1), round(origin_wgs[1], 1))
    data = amap("/v3/place/around", keywords=query, location=f"{anchor[0]:.6f},{anchor[1]:.6f}",
                radius="20000", sortrule="distance", offset="25", extensions="base")
    found = []
    for poi in data.get("pois", []):
        if isinstance(poi.get("location"), str) and "," in poi["location"]:
            lng, lat = map(float, poi["location"].split(","))
            found.append((metres(origin_wgs, gcj02_to_wgs84(lng, lat)), poi.get("name", "")))
    return sorted(found)[:count]


def find_destination(name, origin_gcj):
    """The place the dialogue chose, looked up the same way the cane searched for it."""
    data = amap("/v3/place/around", keywords=name, location=f"{origin_gcj[0]:.6f},{origin_gcj[1]:.6f}",
                radius="20000", sortrule="distance", offset="25", extensions="base")
    places = [(poi.get("name", ""), poi.get("location", "")) for poi in data.get("pois", [])
              if isinstance(poi.get("location"), str) and "," in poi.get("location", "")]
    if not places:
        raise RuntimeError(f"no place named {name!r} near the demo origin")
    chosen = next((place for place in places if place[0] == name), places[0])
    lng, lat = map(float, chosen[1].split(","))
    return chosen[0], (lng, lat)


def walking_path(origin_gcj, dest_gcj):
    data = amap("/v3/direction/walking", origin=f"{origin_gcj[0]:.6f},{origin_gcj[1]:.6f}",
                destination=f"{dest_gcj[0]:.6f},{dest_gcj[1]:.6f}")
    paths = (data.get("route") or {}).get("paths") or []
    if not paths:
        raise RuntimeError("AMap returned no walking path")
    points = []
    for step in paths[0].get("steps", []):
        for pair in str(step.get("polyline", "")).split(";"):
            if "," in pair:
                point = tuple(map(float, pair.split(",")))
                if not points or point != points[-1]:
                    points.append(point)
    if len(points) < 2:
        raise RuntimeError("walking path has no geometry")
    return [gcj02_to_wgs84(*point) for point in points]


class Walk:
    """A position that moves along a list of WGS-84 points at a constant speed."""

    def __init__(self, points, speed):
        self.points, self.speed = points, speed
        self.segment, self.along = 0, 0.0
        self.length = sum(metres(a, b) for a, b in zip(points, points[1:]))

    @property
    def done(self):
        return self.segment >= len(self.points) - 1

    def advance(self, seconds):
        remaining = self.speed * seconds
        while remaining > 0 and not self.done:
            a, b = self.points[self.segment], self.points[self.segment + 1]
            span = metres(a, b)
            if self.along + remaining < span:
                self.along += remaining
                break
            remaining -= span - self.along
            self.segment, self.along = self.segment + 1, 0.0
        return self.position()

    def position(self):
        if self.done:
            return self.points[-1]
        a, b = self.points[self.segment], self.points[self.segment + 1]
        span = metres(a, b) or 1.0
        t = self.along / span
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


# ---- the cane ----------------------------------------------------------------------------
class Cane:
    def __init__(self, release):
        import tomllib

        release = Path(release).expanduser()
        camera = tomllib.loads((release / "config" / "camera.unified.toml").read_text("utf-8"))
        pair_dir = Path(camera["navigation"]["phone_pair"])
        pair_dir = pair_dir if pair_dir.is_absolute() else release / pair_dir
        pair = json.loads((pair_dir / "pair.json").read_text("utf-8"))
        self.token = pair["token"]
        self.endpoint = f"https://{pair['address']}:8443/api/phone/location"
        # Trust the pairing's own root certificate. Python 3.13+ also demands strict RFC 5280 extensions,
        # which a small private CA may not carry; the chain and the address are still verified.
        context = ssl.create_default_context(cadata=(pair_dir / "root.cer").read_bytes())
        context.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                  urllib.request.HTTPSHandler(context=context))

    def report(self, lng, lat, accuracy=5.0):
        body = json.dumps({"lng": lng, "lat": lat, "accuracy": accuracy, "token": self.token}).encode()
        request = urllib.request.Request(self.endpoint, data=body, headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=5) as response:
            return response.status == 200

    @staticmethod
    def _local(url):
        with NO_PROXY.open(url, timeout=4) as response:
            return json.load(response)

    def navigation(self):
        return (self._local("http://127.0.0.1:8080/api/status").get("nav") or {})

    def chosen_place(self):
        dialogue = self._local("http://127.0.0.1:8000/assistant/status")["assistant"].get("destination_dialogue") or {}
        return dialogue.get("chosen")


def run(args):
    origin_gcj = tuple(args.origin_gcj)
    origin = gcj02_to_wgs84(*origin_gcj)
    cane = Cane(args.release)
    say(f"reporting a simulated location near GCJ-02 {origin_gcj[0]:.4f},{origin_gcj[1]:.4f} to {cane.endpoint}")
    say("stop location sharing in the iPhone app while this runs; Ctrl-C ends the simulation")
    try:
        nearest = cane_candidates("地铁站", origin)
        say("check: the cane's search works from here. Nearest metro stations: "
            + ", ".join(f"{name} {distance:.0f} m" for distance, name in nearest))
    except Exception as error:
        say("WARNING: a search from this start point fails, so K2 will fail too:", error)
    position, walk, chosen, was_active, ended_at, failures, blind = origin, None, None, False, None, 0, 0
    last = time.monotonic()
    while True:
        now = time.monotonic()
        elapsed, last = now - last, now
        try:
            nav = cane.navigation()
            chosen = cane.chosen_place() or nav.get("pending_destination") or chosen
            active = bool(nav.get("active"))
            if blind:
                say("status is available again")
            blind = 0
        except Exception as error:  # the service may be restarting
            nav, active = {}, was_active
            blind += 1
            if blind in (1, 10) or blind % 60 == 0:
                say(f"status not available ({type(error).__name__}: {getattr(error, 'reason', error)}); retrying")
        if active and not was_active:
            ended_at = None
            if args.no_walk or not chosen:
                say("navigation started; holding the fixed point" + ("" if chosen else " (destination unknown)"))
            else:
                try:
                    name, dest_gcj = find_destination(chosen, origin_gcj)
                    walk = Walk(walking_path(origin_gcj, dest_gcj), args.speed)
                    say(f"navigation started to {name}: walking {walk.length:.0f} m at {args.speed} m/s "
                        f"(about {walk.length / args.speed / 60:.1f} min)")
                except Exception as error:
                    walk = None
                    say("could not prepare the walk, holding the fixed point:", error)
        if was_active and not active:
            say("navigation ended; returning to the start point in 8 s")
            walk, ended_at = None, now
        if ended_at is not None and now - ended_at > 8:
            position, ended_at, chosen = origin, None, None
        if walk is not None and active and not nav.get("waiting_crossing"):
            position = walk.advance(elapsed)
            if walk.done:
                say("reached the end of the route")
                walk = None
        was_active = active
        try:
            cane.report(*position)
            if failures:
                say("reporting again")
            failures = 0
        except Exception as error:
            failures += 1
            if failures in (1, 10) or failures % 60 == 0:
                say(f"could not report the location ({type(error).__name__}: {getattr(error, 'reason', error)}); retrying")
        time.sleep(max(0.0, args.interval - (time.monotonic() - now)))


def dry_run(args):
    origin_gcj = tuple(args.origin_gcj)
    origin = gcj02_to_wgs84(*origin_gcj)
    say(f"origin GCJ-02 {origin_gcj} -> WGS-84 {tuple(round(v, 6) for v in origin)}, "
        f"grid anchor {round(origin[0], 1)},{round(origin[1], 1)}")
    for query in ("地铁站", args.dest):
        say(f"the cane would offer for {query!r}: "
            + ", ".join(f"{name} {distance:.0f} m" for distance, name in cane_candidates(query, origin)))
        time.sleep(0.5)
    name, dest_gcj = find_destination(args.dest, origin_gcj)
    time.sleep(0.5)
    walk = Walk(walking_path(origin_gcj, dest_gcj), args.speed)
    say(f"destination: {name} at GCJ-02 {dest_gcj}")
    say(f"route: {len(walk.points)} points, {walk.length:.0f} m, {walk.length / args.speed / 60:.1f} min at {args.speed} m/s")
    seconds = 0
    while not walk.done and seconds < 3600:
        lng, lat = walk.advance(30)
        seconds += 30
        say(f"  t+{seconds:>4}s  {lng:.6f},{lat:.6f}")
    back = wgs84_to_gcj02(*gcj02_to_wgs84(*origin_gcj))
    say(f"round-trip conversion error at the origin: {metres(origin_gcj, back):.3f} m")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--release", default=DEFAULT_RELEASE, help="release directory on the Pi")
    parser.add_argument("--origin-gcj", type=lambda text: [float(v) for v in text.split(",")],
                        default=list(DEFAULT_ORIGIN_GCJ), metavar="LNG,LAT", help="start point as AMap shows it")
    parser.add_argument("--speed", type=float, default=1.3,
                        help="simulated walking speed in m/s; 3 shortens a demo without skipping prompts")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between reports (the cane expires a fix after 15 s)")
    parser.add_argument("--no-walk", action="store_true", help="hold the fixed point even while navigating")
    parser.add_argument("--dry-run", action="store_true", help="only rehearse the map requests; needs --dest")
    parser.add_argument("--dest", help="place name for --dry-run")
    args = parser.parse_args()
    if len(args.origin_gcj) != 2 or not 0.2 <= args.speed <= 6 or not 0.5 <= args.interval <= 5:
        parser.error("check --origin-gcj, --speed (0.2-6) and --interval (0.5-5)")
    try:
        if args.dry_run:
            if not args.dest:
                parser.error("--dry-run needs --dest")
            dry_run(args)
        else:
            run(args)
    except KeyboardInterrupt:
        say("stopped; the cane will report that the phone location expired after about 15 s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
