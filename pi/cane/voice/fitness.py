"""Read the official 24/7 FITNESS store directory."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Annotated, Callable, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..nav.coords import haversine_m

DIRECTORY: Final = "https://247.fitness/app-api/cms/store/"
MAX_DIRECTORY_BYTES: Final = 256 * 1024
CACHE_SECONDS: Final = 300.0


class DirectoryUnavailable(RuntimeError):
    """The published branch list could not be loaded."""


class _Record(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore", strict=True)


class _Store(_Record):
    storeId: Annotated[int, Field(gt=0)]
    storeName: Annotated[str, Field(min_length=1, max_length=160)]
    status: int
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)] | None = None
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)] | None = None


class _Area(_Record):
    areaName: Annotated[str, Field(min_length=1, max_length=80)]
    stores: Annotated[list[_Store], Field(max_length=500)]


class _Country(_Record):
    countryCode: str
    areaNodeList: Annotated[list[_Area], Field(max_length=64)]


class _Countries(_Record):
    countryNodes: Annotated[list[_Country], Field(max_length=32)]


class _Reply(_Record):
    code: Literal[0]
    data: _Countries


@dataclass(frozen=True, slots=True)
class Branch:
    id: int
    name: str
    area: str
    lng: float
    lat: float


def parse_directory(data: bytes) -> tuple[Branch, ...]:
    """Return the operating stores with valid published positions."""
    try:
        directory = _Reply.model_validate_json(data)
    except ValidationError as exc:
        raise DirectoryUnavailable("invalid fitness directory") from exc
    unique: dict[int, Branch] = {}
    for country in directory.data.countryNodes:
        for area in country.areaNodeList:
            for store in area.stores:
                if store.status != 1 or store.longitude is None or store.latitude is None:
                    continue
                if (store.longitude, store.latitude) == (0, 0):
                    continue
                unique[store.storeId] = Branch(
                    store.storeId, f"24/7 FITNESS {store.storeName.strip()}",
                    area.areaName, store.longitude, store.latitude,
                )
    if not unique:
        raise DirectoryUnavailable("no operating branches in directory")
    return tuple(unique.values())


def download_directory() -> bytes:
    """Fetch one public directory without sending any user query or position."""
    import httpx2

    limits = httpx2.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=15)
    timeout = httpx2.Timeout(connect=4, read=8, write=4, pool=4)
    transport = httpx2.HTTPTransport(
        http2=True, limits=limits, retries=1,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    data = bytearray()
    try:
        with httpx2.Client(transport=transport, timeout=timeout) as client, client.stream(
            "GET", DIRECTORY, params={"lang": "zh-HK"},
            headers={"tenant-id": "1", "User-Agent": "EE3070-Cane/1.0", "Accept": "application/json"},
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                if len(data) + len(chunk) > MAX_DIRECTORY_BYTES:
                    raise DirectoryUnavailable("fitness directory exceeded size limit")
                data.extend(chunk)
    except httpx2.HTTPError as exc:
        raise DirectoryUnavailable("fitness directory request failed") from exc
    return bytes(data)


class FitnessDirectory:
    """Cache the public catalogue for repeat searches on the same Pi."""

    def __init__(self, fetch: Callable[[], bytes] = download_directory) -> None:
        self._fetch = fetch
        self._cache: tuple[Branch, ...] = ()
        self._expires = 0.0
        self._lock = Lock()

    def branches(self) -> tuple[Branch, ...]:
        with self._lock:
            if monotonic() >= self._expires:
                self._cache = parse_directory(self._fetch())
                self._expires = monotonic() + CACHE_SECONDS
            return self._cache


def nearby(branches: tuple[Branch, ...], origin: tuple[float, float]) -> tuple[Branch, ...]:
    """Keep branches in the local search radius, preserving the full ranking input."""
    return tuple(branch for branch in branches if haversine_m(*origin, branch.lng, branch.lat) <= 50000)
