"""Diagnostics client allowlist and browser same-origin checks."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


@dataclass(frozen=True, slots=True)
class DiagnosticsAccess:
    networks: tuple[IPv4Network | IPv6Network, ...]

    @classmethod
    def from_allowlist(cls, allowlist: list[str]) -> DiagnosticsAccess:
        return cls(tuple(ip_network(entry, strict=False) for entry in allowlist))

    def require_client(self, request: Request) -> None:
        """Accept only the TCP client, never proxy-supplied forwarding headers."""
        host = request.client.host if request.client else ""
        try:
            address = ip_address(host)
        except ValueError:
            raise HTTPException(403, "diagnostics not allowed from this address") from None
        if not any(address in network for network in self.networks):
            raise HTTPException(403, "diagnostics not allowed from this address")

    def require_control(self, request: Request) -> None:
        """Allow same-origin browsers and local CLI clients without Origin."""
        self.require_client(request)
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site not in ("same-origin", "none"):
            raise HTTPException(403, "controls require a same-origin request")
        origin = request.headers.get("origin")
        if origin is None:
            return
        try:
            supplied = urlsplit(origin)
            expected = urlsplit(str(request.base_url))
            same = (
                supplied.scheme in ("http", "https")
                and supplied.scheme == expected.scheme
                and supplied.hostname == expected.hostname
                and (supplied.port or (443 if supplied.scheme == "https" else 80))
                == (expected.port or (443 if expected.scheme == "https" else 80))
                and not supplied.username and not supplied.password
                and supplied.path in ("", "/") and not supplied.query and not supplied.fragment
            )
        except ValueError:
            raise HTTPException(403, "controls require a same-origin request") from None
        if not same:
            raise HTTPException(403, "controls require a same-origin request")
