"""Place search for the Pi 5, with a local ranking input for known brands."""

from __future__ import annotations

from ..nav.amap import AmapError, NetworkError
from ..nav.coords import wgs84_to_gcj02
from ..nav.providers import MapClient, map_frame
from .destinations import normalise
from .fitness import DirectoryUnavailable, FitnessDirectory, nearby
from .places import PlaceWorkerError, ProviderPlace, public_search_region


def _wgs84(lng: float, lat: float) -> tuple[float, float]:
    """Invert the project's offline coordinate conversion for POI comparisons."""
    raw_lng, raw_lat = lng, lat
    for _ in range(4):
        converted_lng, converted_lat = wgs84_to_gcj02(raw_lng, raw_lat)
        raw_lng += lng - converted_lng
        raw_lat += lat - converted_lat
    return raw_lng, raw_lat


class PiPlaceSearch:
    def __init__(self, amap: MapClient | None, directory: FitnessDirectory | None = None) -> None:
        self.amap = amap
        self.directory = directory or FitnessDirectory()

    def search(self, query: str, origin: tuple[float, float] | None = None) -> tuple[ProviderPlace, ...]:
        if normalise(query) == "247fitness" and origin is not None:
            try:
                branches = nearby(self.directory.branches(), origin)
            except DirectoryUnavailable as exc:
                raise PlaceWorkerError from exc
            return tuple(
                ProviderPlace(id=f"fitness:{branch.id}", name=branch.name, address=branch.area,
                              lng=branch.lng, lat=branch.lat, coordinate_frame="WGS84")
                for branch in branches
            )
        if self.amap is None:
            raise PlaceWorkerError("A map service key is required for map keyword searches")
        shifted = map_frame(self.amap) == "gcj02"
        region = public_search_region(origin) if origin is not None else None
        decimals = getattr(self.amap, "search_decimals", None)
        if origin is not None and decimals is not None:
            # A service whose search only reaches a couple of kilometres needs a nearer anchor.
            region = (round(origin[0], decimals), round(origin[1], decimals))
        if region is not None and shifted:
            region = wgs84_to_gcj02(*region)
        try:
            places = self.amap.search_places(query, region)
        except (AmapError, NetworkError) as exc:
            raise PlaceWorkerError from exc
        results = []
        for place in places:
            lng, lat = _wgs84(place.lng, place.lat) if shifted else (place.lng, place.lat)
            results.append(ProviderPlace(id=place.id[:128], name=place.name[:256], address=place.address[:512],
                                         lng=lng, lat=lat, coordinate_frame="WGS84"))
        return tuple(results)
