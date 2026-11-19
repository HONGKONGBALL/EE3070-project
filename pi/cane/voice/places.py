"""Place choices and current-query state for voice destination input."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Literal, NotRequired, TypeAlias, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import Destination
from ..nav.coords import haversine_m
from .destinations import normalise


class PlaceChoice(BaseModel):
    """One provider result that can be named and selected."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    address: str = Field(max_length=512)


class ProviderPlace(PlaceChoice):
    """A place with a position in its provider's coordinate frame."""

    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    coordinate_frame: Literal["GCJ02", "WGS84"]


class PlacesReply(BaseModel):
    """Validated search result from the place lookup process."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    status: Literal["ok"]
    candidates: Annotated[tuple[ProviderPlace | PlaceChoice, ...], Field(max_length=160)]


class DetailReply(BaseModel):
    """Validated location of the explicitly selected place."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    status: Literal["ok"]
    place: ProviderPlace


class PlaceWorkerError(RuntimeError):
    """The place lookup process did not return a usable result."""


@dataclass(frozen=True, slots=True)
class PlaceWorker:
    """Run lookups with the configured navigation toolchain."""

    python: str
    script: Path

    def _call(self, request: str) -> bytes:
        command = [self.python, str(self.script)] if self.python else [str(self.script)]
        try:
            completed = subprocess.run(
                command, input=request.encode("utf-8"),
                capture_output=True, check=True, timeout=25,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PlaceWorkerError from error
        if len(completed.stdout) > 256 * 1024:
            raise PlaceWorkerError
        return completed.stdout

    def search(
        self,
        query: str,
        city: str,
        *,
        nearby: bool = False,
        region: tuple[float, float] | None = None,
    ) -> tuple[PlaceChoice, ...]:
        """Find named places by keyword."""
        request: dict[str, str | bool | dict[str, float] | None] = {"query": query, "city": city or None}
        if nearby:
            request["nearby"] = True
        if region is not None:
            request["region"] = {"lng": region[0], "lat": region[1]}
        payload = self._call(json.dumps(request))
        try:
            choices = PlacesReply.model_validate_json(payload).candidates
        except ValidationError as error:
            raise PlaceWorkerError from error
        return choices if nearby else choices[:5]


    def detail(self, candidate_id: str) -> ProviderPlace:
        """Resolve coordinates for a selection in the current result list."""
        payload = self._call(json.dumps({"id": candidate_id}))
        try:
            result = DetailReply.model_validate_json(payload).place
        except ValidationError as error:
            raise PlaceWorkerError from error
        if result.id != candidate_id or result.coordinate_frame != "GCJ02":
            raise PlaceWorkerError
        return result


def public_search_region(origin: tuple[float, float]) -> tuple[float, float]:
    """Choose a fixed map grid anchor while retaining the exact origin locally."""
    return round(origin[0], 1), round(origin[1], 1)


class ChoiceView(TypedDict):
    id: str
    name: str
    address: str
    distance_m: NotRequired[int]


SearchState: TypeAlias = Literal[
    "idle", "waiting_origin", "searching", "results", "empty", "resolving", "selected", "error",
]


class SearchView(TypedDict):
    search_id: str
    query: str
    candidates: list[ChoiceView]
    search_state: SearchState
    distance_ordered: bool


class DestinationSearch:
    """Current search state, used under the controller's state lock."""

    def __init__(self) -> None:
        self.id: str = ""
        self.query: str = ""
        self.state: SearchState = "idle"
        self.choices: tuple[PlaceChoice, ...] = ()
        self.distances: dict[str, float] = {}
        self.worker: PlaceWorker | None = None

    def reset(self) -> None:
        """Expire outstanding lookup and selection callbacks."""
        self.id = ""
        self.query = ""
        self.state = "idle"
        self.choices = ()
        self.distances = {}
        self.worker = None

    def begin(self, query: str, worker: PlaceWorker | None) -> str:
        """Give each new query a unique identity."""
        self.id = uuid4().hex
        self.query = query
        self.state = "searching"
        self.choices = ()
        self.distances = {}
        self.worker = worker
        return self.id

    def finish(self, search_id: str, choices: tuple[PlaceChoice, ...] | None,
               origin: tuple[float, float] | None = None) -> None:
        """Publish results only for the search that is still visible."""
        if search_id != self.id or self.state != "searching":
            return
        candidates = choices or ()
        if origin is not None:
            distances = {
                place.id: haversine_m(*origin, place.lng, place.lat)
                for place in candidates
                if isinstance(place, ProviderPlace) and place.coordinate_frame == "WGS84"
            }
            if len(distances) == len(candidates):
                self.distances = distances
                candidates = tuple(sorted(candidates, key=lambda place: distances[place.id]))
        self.choices = candidates[:5]
        self.state = "error" if choices is None else "results" if choices else "empty"

    def choice(self, search_id: str, candidate_id: str) -> PlaceChoice | None:
        """Accept candidates only from the current visible result list."""
        if search_id != self.id or self.state != "results":
            return None
        return next((candidate for candidate in self.choices if candidate.id == candidate_id), None)

    def snapshot(self) -> SearchView:
        """Project only display fields to the candidate list."""
        candidates: list[ChoiceView] = []
        for choice in self.choices:
            item: ChoiceView = {"id": choice.id, "name": choice.name, "address": choice.address}
            if choice.id in self.distances:
                item["distance_m"] = round(self.distances[choice.id])
            candidates.append(item)
        return {
            "search_id": self.id, "query": self.query, "search_state": self.state,
            "candidates": candidates, "distance_ordered": bool(self.distances),
        }


def preset_choices(query: str, destinations: Sequence[Destination]) -> tuple[PlaceChoice, ...]:
    """Return matching local names and aliases in configuration order."""
    term = normalise(query)
    if not term:
        return ()
    choices = tuple(
        PlaceChoice(id=entry.id, name=entry.name, address=entry.address)
        for entry in destinations
        if any(term in normalise(label) for label in (entry.name, *entry.aliases))
    )
    return choices[:5]
