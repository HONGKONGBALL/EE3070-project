"""Place search for the Pi 5, with a local ranking input for known brands."""

from __future__ import annotations

from ..nav.amap import AmapClient, AmapError, NetworkError
from ..nav.coords import wgs84_to_gcj02
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
    def __init__(self, amap: AmapClient | None, directory: FitnessDirectory | None = None) -> None:
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
            raise PlaceWorkerError("AMAP_KEY is required for map keyword searches")
        region = wgs84_to_gcj02(*public_search_region(origin)) if origin is not None else None
        try:
            places = self.amap.search_places(query, region)
        except (AmapError, NetworkError) as exc:
            raise PlaceWorkerError from exc
        results = []
        for place in places:
            lng, lat = _wgs84(place.lng, place.lat)
            results.append(ProviderPlace(id=place.id[:128], name=place.name[:256], address=place.address[:512],
                                         lng=lng, lat=lat, coordinate_frame="WGS84"))
        return tuple(results)
