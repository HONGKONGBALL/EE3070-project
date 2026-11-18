"""Read the Pi's generated phone pairing identity."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field

PHONE_PORT = 8443
LAN_NETWORKS = tuple(IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class PhonePair(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    address: IPv4Address
    token: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{32,128}$", repr=False)]

    @classmethod
    def load(cls, directory: Path) -> PhonePair:
        """Load the identity used by the HTTPS phone service."""
        return cls.model_validate_json((directory / "pair.json").read_bytes())
