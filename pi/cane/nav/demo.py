"""Explicit synthetic walking route for software demonstrations only.

Coordinates are in a tiny synthetic area around (0, 0), never a real campus.
The adapter refuses phone fixes elsewhere rather than fabricate live routing.
"""

from __future__ import annotations

import math
from typing import Final

from .amap import AmapError, Route, Step
from .coords import haversine_m

DEMO_ORIGIN: Final = (0.0, 0.0)
DEMO_TURN: Final = (0.0, 100.0 / 111195.0)
DEMO_CROSSING_END: Final = (30.0 / 111195.0, DEMO_TURN[1])
DEMO_DESTINATION: Final = (60.0 / 111195.0, DEMO_TURN[1])
DEMO_WAYPOINTS: Final = (DEMO_ORIGIN, DEMO_TURN, DEMO_CROSSING_END, DEMO_DESTINATION)


class DemoRouteError(AmapError):
    """A non-demo location was supplied to the explicit demo adapter."""


class DemoRouteClient:
    """Keyless AmapClient-compatible adapter, deliberately marked as demo."""

    is_demo: Final = True

    def convert_gps(self, lng: float, lat: float) -> tuple[float, float]:
        """Return only synthetic coordinates, without any GCJ transform."""
        point = (lng, lat)
        if not all(math.isfinite(v) for v in point) or haversine_m(*point, *DEMO_ORIGIN) > 350:
            raise DemoRouteError("demo routing only accepts synthetic coordinates near (0, 0)")
        return point

    def walking_route(self, origin: tuple[float, float], dest: tuple[float, float]) -> Route:
        """Build an L-shaped demo route, including a manual crossing handoff."""
        start = self.convert_gps(*origin)
        _ = self.convert_gps(*dest)
        if haversine_m(*dest, *DEMO_DESTINATION) > 0.5:
            raise DemoRouteError("demo destination must be the declared synthetic endpoint")
        # Replanning from a demo deviation reconnects to the same crossing.
        length = haversine_m(*start, *DEMO_TURN)
        return Route(length + 60, length + 60, [
            Step("演示：向北步行，到路口右转", length, [start, DEMO_TURN], action="右转"),
            Step("演示：前方人行横道，请自行判断并确认", 30, [DEMO_TURN, DEMO_CROSSING_END]),
            Step("演示：向东步行到达演示终点", 30, [DEMO_CROSSING_END, DEMO_DESTINATION]),
        ])

    def geocode(self, address: str, city: str = "") -> tuple[float, float]:
        """Only the named demonstration destination has synthetic coordinates."""
        if address.strip() not in {"演示终点", "演示目的地", "demo destination"}:
            raise DemoRouteError("demo geocoding only accepts the explicit demo destination")
        return DEMO_DESTINATION
