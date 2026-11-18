"""Current-session event acceptance and permission at the host boundary."""

from __future__ import annotations

from typing import Final

from project import Model
from pydantic import Field

FIX_TTL_MS: Final = 5000


class Identity(Model):
    """Current session, generation and route identity."""

    session_id: str
    generation: int = Field(ge=0)
    route_version: int = Field(ge=0)


class Event(Identity):
    """Parsed event eligible for current-session output."""

    type: str
    event_id: int = Field(ge=1)
    ttl_ms: int = Field(gt=0, le=2000)
    issued_at_ms: int = Field(ge=0)
    fix_seq: int = Field(ge=0)
    fix_age_ms: int = Field(default=0, ge=0)
    distance_m: float = 0
    confirmation_source: str = ""


class Decision(Model):
    """Output gate disposition and machine-readable reason."""

    accepted: bool
    reason: str


class Gate:
    """Mutable host lease and bounded event deduplication state."""

    def __init__(self, identity: Identity, *, integrated: bool = False) -> None:
        """Create an output gate for one current navigation identity."""
        self.identity: Identity = identity
        self.integrated: bool = integrated
        self.permission_until: int = 0
        self.seen: list[int] = []

    def permit(self, now_ms: int, lease_ms: int = 3000) -> None:
        """Grant a bounded lease measured on the host monotonic clock."""
        self.permission_until = now_ms + min(max(lease_ms, 0), 3000)

    def accept(
        self, event: Event, *, received_at_ms: int, now_ms: int, transit_age_ms: int = 0
    ) -> Decision:
        """Check identity, age, location freshness, lease and deduplication."""
        if transit_age_ms < 0:
            return Decision(accepted=False, reason="TIMING_REFERENCE")
        if (event.session_id, event.generation, event.route_version) != (
            self.identity.session_id,
            self.identity.generation,
            self.identity.route_version,
        ):
            return Decision(accepted=False, reason="IDENTITY")
        age = now_ms - received_at_ms + transit_age_ms
        if age < 0 or age >= event.ttl_ms:
            return Decision(accepted=False, reason="EVENT_EXPIRED")
        if event.fix_age_ms + age >= FIX_TTL_MS:
            return Decision(accepted=False, reason="FIX_EXPIRED")
        if self.integrated and now_ms >= self.permission_until:
            return Decision(accepted=False, reason="PERMISSION_EXPIRED")
        if event.event_id in self.seen:
            return Decision(accepted=False, reason="DUPLICATE")
        self.seen = [*self.seen[-63:], event.event_id]
        return Decision(accepted=True, reason="OK")
